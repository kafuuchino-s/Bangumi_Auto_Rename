from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import quote_plus, unquote, urljoin

import requests

try:
    from scrapling.fetchers import DynamicFetcher, FetcherSession
except ImportError:
    DynamicFetcher = None
    FetcherSession = None

from ...config.config_manager import cm
from ...logger import logger
from ..extractor import SUBTITLE_EXTENSIONS
from .base import (
    SubtitleCandidate,
    SubtitleDownloadResult,
    SubtitleProvider,
    SubtitleThreadPackage,
    SubtitleThreadPackageLink,
)

_THREAD_LINK_RE = re.compile(r"(?:^|/)forum\.php\?mod=viewthread&tid=\d+")
_THREAD_ID_RE = re.compile(r"(?:^|[?&])tid=(\d+)")
_THREAD_PAGE_RE = re.compile(r"(?:^|[?&])page=(\d+)")
_POST_CONTAINER_RE = re.compile(r"^post_(\d+)$")
_ATTACHMENT_RE = re.compile(r"attachment&aid=")
_ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_CLOUD_HOST_KEYWORDS = (
    "pan.acgrip.com",
    "lanzou",
    "123pan",
    "aliyundrive",
    "alipan",
    "pan.baidu.com",
)
_BATCH_KEYWORDS = (
    "全集",
    "全季",
    "合集",
    "batch",
    "complete",
    "full",
)
_REVISION_KEYWORDS = (
    "修正版",
    "修订",
    "修正",
    "校对",
    "v2",
    "v3",
    "rev",
    "fix",
)
_PATCH_KEYWORDS = ("补丁", "patch")
_SPECIAL_KEYWORDS = (
    "特典",
    "ncop",
    "nced",
    "pv",
    "cm",
    "ova",
    "oad",
    "sp",
    "ex",
)
_FONT_KEYWORDS = ("font", "fonts", "字体")
_SIMPLIFIED_KEYWORDS = ("简体", "简中", "chs", "gb")
_TRADITIONAL_KEYWORDS = ("繁体", "繁中", "cht", "big5")
_BILINGUAL_KEYWORDS = ("双语", "简日", "繁日", "中日")
_MAX_THREAD_PAGES = 3


class ACGRIPProvider(SubtitleProvider):
    """基于 Scrapling 的 ACGRIP 字幕源 provider。"""

    def __init__(self) -> None:
        self.base_url = cm.get_config("subtitle_auto_fetch_acgrip_base_url") or (
            "https://bbs.acgrip.com"
        )
        self.timeout = int(cm.get_config("subtitle_auto_fetch_timeout_seconds") or 30)
        self.browser_enabled = bool(
            cm.get_config("subtitle_auto_fetch_browser_enabled")
        )

    def search(self, keyword: str, limit: int = 10) -> List[SubtitleCandidate]:
        keyword = str(keyword or "").strip()
        if not keyword:
            return []

        search_url = (
            f"{self.base_url}/search.php?mod=forum&searchsubmit=yes"
            f"&srchtxt={quote_plus(keyword)}"
        )
        logger.info(f"[字幕抓取][ACGRIP] 搜索: {keyword}")

        try:
            page = self._fetch_page(search_url)
        except Exception as exc:
            logger.error(f"[字幕抓取][ACGRIP] 搜索失败: {exc}")
            return []

        anchors = page.css("a")
        candidates: List[SubtitleCandidate] = []
        seen_urls = set()

        for anchor in anchors:
            href = (anchor.attrib.get("href") or "").strip()
            title = self._extract_text(anchor)
            if not href or not title:
                continue
            if not _THREAD_LINK_RE.search(href):
                continue

            detail_url = urljoin(f"{self.base_url}/", href)
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            snippet = self._extract_result_snippet(anchor)
            candidate = SubtitleCandidate(
                title=title,
                detail_url=detail_url,
                source="acgrip",
                snippet=snippet,
                metadata={"keyword": keyword},
            )
            candidates.append(candidate)
            if len(candidates) >= limit:
                break

        logger.info(f"[字幕抓取][ACGRIP] 搜索命中 {len(candidates)} 条候选")
        return candidates

    def prepare_candidate(self, candidate: SubtitleCandidate) -> SubtitleCandidate:
        try:
            detail_page = self._fetch_page(candidate.detail_url)
            self._fill_candidate_downloads(candidate, detail_page)
        except Exception as exc:
            logger.warning(f"[字幕抓取][ACGRIP] 预加载候选详情失败: {exc}")
        return candidate

    def load_thread_packages(self, candidate: SubtitleCandidate) -> SubtitleCandidate:
        if candidate.thread_packages and candidate.pages_scanned > 0:
            return candidate

        try:
            pages, truncated = self._fetch_thread_pages(candidate.detail_url)
        except Exception as exc:
            logger.warning(f"[字幕抓取][ACGRIP] 深解析帖子失败: {exc}")
            return candidate

        packages: List[SubtitleThreadPackage] = []
        for page_number, page in pages:
            packages.extend(self._extract_thread_packages(page, page_number))

        candidate.thread_packages = packages
        candidate.pages_scanned = len(pages)
        candidate.pagination_truncated = truncated
        if packages:
            self._populate_candidate_urls_from_packages(candidate)
        elif pages:
            self._fill_candidate_downloads(candidate, pages[0][1])
        return candidate

    def download(
        self,
        candidate: SubtitleCandidate,
        destination_dir: Path,
        package: Optional[SubtitleThreadPackage] = None,
    ) -> SubtitleDownloadResult:
        destination_dir.mkdir(parents=True, exist_ok=True)

        selected_package = package
        candidate = self.load_thread_packages(candidate)
        if selected_package is None and candidate.thread_packages:
            selected_package = self._pick_first_downloadable_package(
                candidate.thread_packages
            )

        if not candidate.attachment_urls and not candidate.external_urls:
            try:
                detail_page = self._fetch_page(candidate.detail_url)
                self._fill_candidate_downloads(candidate, detail_page)
            except Exception as exc:
                logger.error(f"[字幕抓取][ACGRIP] 读取详情页失败: {exc}")
                return SubtitleDownloadResult(
                    candidate=candidate,
                    downloaded_path=None,
                    download_url=None,
                    status="failed",
                    error=f"读取详情页失败: {exc}",
                    selected_package=selected_package,
                )

        download_url = self._pick_download_url(candidate, selected_package)
        if not download_url:
            return SubtitleDownloadResult(
                candidate=candidate,
                downloaded_path=None,
                download_url=None,
                status="no_download",
                error="未找到可直接下载的附件链接",
                selected_package=selected_package,
            )

        filename = self._infer_filename(candidate, download_url, selected_package)
        downloaded_path = destination_dir / filename

        try:
            downloaded_path = self._download_file(download_url, downloaded_path)
        except Exception as exc:
            logger.error(f"[字幕抓取][ACGRIP] 下载失败: {exc}")
            return SubtitleDownloadResult(
                candidate=candidate,
                downloaded_path=None,
                download_url=download_url,
                status="failed",
                error=str(exc),
                selected_package=selected_package,
            )

        logger.info(f"[字幕抓取][ACGRIP] 下载完成: {downloaded_path}")
        return SubtitleDownloadResult(
            candidate=candidate,
            downloaded_path=downloaded_path,
            download_url=download_url,
            status="success",
            selected_package=selected_package,
        )

    def _fetch_page(self, url: str):
        if DynamicFetcher is None or FetcherSession is None:
            raise RuntimeError("未安装 scrapling，请先安装该依赖")

        if self.browser_enabled:
            logger.info(f"[字幕抓取][ACGRIP] 动态抓取: {url}")
            return DynamicFetcher.fetch(url)

        with FetcherSession(impersonate="chrome") as session:
            return session.get(url, stealthy_headers=True)

    def _fetch_thread_pages(self, detail_url: str) -> Tuple[List[Tuple[int, Any]], bool]:
        first_page = self._fetch_page(detail_url)
        pages: List[Tuple[int, Any]] = [(1, first_page)]
        thread_id = self._extract_thread_id(detail_url)
        page_numbers = self._extract_thread_page_numbers(first_page, thread_id)
        max_page = max(page_numbers) if page_numbers else 1

        for page_number in page_numbers:
            if page_number <= 1 or page_number > _MAX_THREAD_PAGES:
                continue
            page_url = self._build_thread_page_url(thread_id, page_number, detail_url)
            try:
                pages.append((page_number, self._fetch_page(page_url)))
            except Exception as exc:
                logger.warning(
                    f"[字幕抓取][ACGRIP] 读取帖子第 {page_number} 页失败: {exc}"
                )

        return pages, max_page > _MAX_THREAD_PAGES

    def _extract_thread_page_numbers(self, page, thread_id: Optional[str]) -> List[int]:
        page_numbers = {1}
        for anchor in page.css("a"):
            href = (anchor.attrib.get("href") or "").strip()
            if not href or "viewthread" not in href:
                continue
            absolute_url = urljoin(f"{self.base_url}/", href)
            if thread_id and self._extract_thread_id(absolute_url) != thread_id:
                continue
            match = _THREAD_PAGE_RE.search(absolute_url)
            if not match:
                continue
            try:
                page_number = int(match.group(1))
            except ValueError:
                continue
            if page_number > 0:
                page_numbers.add(page_number)
        return sorted(page_numbers)

    def _build_thread_page_url(
        self,
        thread_id: Optional[str],
        page_number: int,
        fallback_url: str,
    ) -> str:
        if not thread_id:
            return fallback_url
        return f"{self.base_url}/forum.php?mod=viewthread&tid={thread_id}&page={page_number}"

    def _extract_thread_id(self, url: str) -> Optional[str]:
        match = _THREAD_ID_RE.search(url or "")
        return match.group(1) if match else None

    def _extract_text(self, node) -> str:
        text = " ".join(t.strip() for t in node.css("::text").getall() if t.strip())
        return text.strip()

    @staticmethod
    def _collapse_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        normalized = ACGRIPProvider._collapse_whitespace(text)
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit].rstrip()}..."

    def _extract_result_snippet(self, anchor) -> Optional[str]:
        parent = getattr(anchor, "parent", None)
        if not parent:
            return None
        text = " ".join(t.strip() for t in parent.css("::text").getall() if t.strip())
        text = self._collapse_whitespace(text)
        return text[:300] if text else None

    def _extract_thread_packages(
        self,
        detail_page,
        page_number: int,
    ) -> List[SubtitleThreadPackage]:
        packages: List[SubtitleThreadPackage] = []
        floor_index = 0
        for post in detail_page.css('div[id^="post_"]'):
            post_id = (post.attrib.get("id") or "").strip()
            match = _POST_CONTAINER_RE.fullmatch(post_id)
            if not match:
                continue
            floor_index += 1
            package = self._build_package_from_post(post, page_number, floor_index)
            if package is not None:
                packages.append(package)
        return packages

    def _build_package_from_post(
        self,
        post,
        page_number: int,
        floor_index: int,
    ) -> Optional[SubtitleThreadPackage]:
        post_id = (post.attrib.get("id") or "").strip()
        match = _POST_CONTAINER_RE.fullmatch(post_id)
        if not match:
            return None

        links = self._collect_download_links_from_node(post)
        if not links:
            return None

        post_text = self._extract_post_text(post)
        header_text = self._extract_post_header_text(post)
        context_text = self._truncate_text(
            self._collapse_whitespace(f"{header_text} {post_text}"),
            2000,
        )
        floor_label = self._extract_floor_label(post, floor_index)
        package_flags = self._detect_package_flags(post_text, links)

        return SubtitleThreadPackage(
            package_id=f"post-{match.group(1)}",
            page_number=page_number,
            floor_label=floor_label,
            post_author=self._extract_post_author(post, header_text),
            post_time=self._extract_post_time(post, header_text),
            post_text=self._truncate_text(post_text, 1600),
            context_text=context_text,
            links=links,
            has_direct_download=any(link.is_direct_download for link in links),
            package_flags=package_flags,
        )

    def _extract_post_header_text(self, post) -> str:
        header = post.css("div.pi").first
        if not header:
            return ""
        return self._collapse_whitespace(
            str(header.get_all_text(separator=" ", strip=True))
        )

    def _extract_post_text(self, post) -> str:
        for selector in ("td.t_f", "div.pct", "ignore_js_op"):
            node = post.css(selector).first
            if not node:
                continue
            text = self._collapse_whitespace(
                str(node.get_all_text(separator=" ", strip=True))
            )
            if text:
                return text
        return self._collapse_whitespace(
            str(post.get_all_text(separator=" ", strip=True))
        )

    def _extract_floor_label(self, post, floor_index: int) -> str:
        post_anchor = post.css('a[id^="postnum"]').first
        if post_anchor:
            label = self._collapse_whitespace(self._extract_text(post_anchor))
            if label and label not in {"推荐"}:
                return label
        return f"第{floor_index}楼"

    def _extract_post_author(self, post, header_text: str) -> Optional[str]:
        for selector in ("div.pi a.xw1", "div.authi a.xw1", "div.pls a.xi2"):
            node = post.css(selector).first
            if not node:
                continue
            text = self._collapse_whitespace(self._extract_text(node))
            if text:
                return text

        match = re.search(r"(?:^|\s)(?:楼主|沙发|板凳|地板|推荐)?\s*([^\s|]+)\s*发表于", header_text)
        return match.group(1) if match else None

    def _extract_post_time(self, post, header_text: str) -> Optional[str]:
        time_node = post.css("div.pi em").first
        if time_node:
            text = self._collapse_whitespace(self._extract_text(time_node))
            match = re.search(r"发表于\s*(.+)$", text)
            if match:
                return match.group(1).strip()

        match = re.search(r"发表于\s*([0-9]{4}-[0-9]{1,2}-[0-9]{1,2}\s+[0-9:]{5,8})", header_text)
        return match.group(1) if match else None

    def _collect_download_links_from_node(
        self,
        node,
    ) -> List[SubtitleThreadPackageLink]:
        links: List[SubtitleThreadPackageLink] = []
        seen_urls = set()
        for anchor in node.css("a"):
            link = self._build_package_link(anchor)
            if link is None or link.url in seen_urls:
                continue
            seen_urls.add(link.url)
            links.append(link)
        return links

    def _build_package_link(self, anchor) -> Optional[SubtitleThreadPackageLink]:
        href = (anchor.attrib.get("href") or "").strip()
        if not href:
            return None

        absolute_url = urljoin(f"{self.base_url}/", href)
        href_lower = absolute_url.lower()
        link_text = self._extract_text(anchor)
        filename_hint = self._extract_attachment_filename(link_text, absolute_url)
        suffix = Path(unquote(absolute_url.split("?")[0])).suffix.lower()

        if _ATTACHMENT_RE.search(absolute_url):
            if not self._is_downloadable_attachment(filename_hint, absolute_url):
                return None
            return SubtitleThreadPackageLink(
                url=absolute_url,
                kind="attachment",
                label=link_text,
                filename_hint=filename_hint,
                is_direct_download=True,
            )

        if any(host in href_lower for host in _CLOUD_HOST_KEYWORDS):
            return SubtitleThreadPackageLink(
                url=absolute_url,
                kind="external",
                label=link_text,
                filename_hint=filename_hint,
                is_direct_download=suffix in _ARCHIVE_SUFFIXES
                or suffix in SUBTITLE_EXTENSIONS,
            )

        if suffix in _ARCHIVE_SUFFIXES or suffix in SUBTITLE_EXTENSIONS:
            return SubtitleThreadPackageLink(
                url=absolute_url,
                kind="external",
                label=link_text,
                filename_hint=filename_hint,
                is_direct_download=True,
            )
        return None

    def _detect_package_flags(
        self,
        post_text: str,
        links: List[SubtitleThreadPackageLink],
    ) -> List[str]:
        link_text = " ".join(link.filename_hint or link.label for link in links)
        combined = html.unescape(f"{post_text} {link_text}").lower()
        flags: List[str] = []
        if self._contains_any(combined, _BATCH_KEYWORDS):
            flags.append("batch")
        if self._contains_any(combined, _REVISION_KEYWORDS):
            flags.append("revision")
        if self._contains_any(combined, _PATCH_KEYWORDS):
            flags.append("patch")
        if self._contains_any(combined, _SPECIAL_KEYWORDS):
            flags.append("special")
        if self._contains_any(combined, _FONT_KEYWORDS):
            flags.append("font")
        if self._contains_any(combined, _SIMPLIFIED_KEYWORDS):
            flags.append("simplified")
        if self._contains_any(combined, _TRADITIONAL_KEYWORDS):
            flags.append("traditional")
        if self._contains_any(combined, _BILINGUAL_KEYWORDS):
            flags.append("bilingual")
        return flags

    @staticmethod
    def _contains_any(text: str, keywords: Tuple[str, ...]) -> bool:
        return any(keyword in text for keyword in keywords)

    def _fill_candidate_downloads(self, candidate: SubtitleCandidate, detail_page) -> None:
        links = self._collect_download_links_from_node(detail_page)
        attachment_urls, external_urls = self._flatten_links(links)
        candidate.attachment_urls = attachment_urls
        candidate.external_urls = external_urls

    def _populate_candidate_urls_from_packages(self, candidate: SubtitleCandidate) -> None:
        links: List[SubtitleThreadPackageLink] = []
        for package in candidate.thread_packages:
            links.extend(package.links)
        attachment_urls, external_urls = self._flatten_links(links)
        candidate.attachment_urls = attachment_urls
        candidate.external_urls = external_urls

    def _flatten_links(
        self,
        links: List[SubtitleThreadPackageLink],
    ) -> Tuple[List[str], List[str]]:
        attachment_urls: List[str] = []
        external_urls: List[str] = []
        for link in links:
            if link.kind == "attachment":
                attachment_urls.append(link.url)
            elif link.kind == "external":
                external_urls.append(link.url)
        return self._unique(attachment_urls), self._unique(external_urls)

    def _pick_first_downloadable_package(
        self,
        packages: List[SubtitleThreadPackage],
    ) -> Optional[SubtitleThreadPackage]:
        for package in packages:
            if self._pick_package_download_url(package):
                return package
        return None

    def _pick_download_url(
        self,
        candidate: SubtitleCandidate,
        package: Optional[SubtitleThreadPackage] = None,
    ) -> Optional[str]:
        if package is not None:
            download_url = self._pick_package_download_url(package)
            if download_url:
                return download_url

        if candidate.attachment_urls:
            return candidate.attachment_urls[0]

        for url in candidate.external_urls:
            suffix = Path(url.split("?")[0]).suffix.lower()
            if suffix in _ARCHIVE_SUFFIXES or suffix in SUBTITLE_EXTENSIONS:
                return url
        return None

    def _pick_package_download_url(
        self,
        package: SubtitleThreadPackage,
    ) -> Optional[str]:
        scored_links = []
        for index, link in enumerate(package.links):
            if not link.is_direct_download:
                continue
            scored_links.append((self._score_package_link(link), -index, link.url))
        if not scored_links:
            return None
        scored_links.sort(reverse=True)
        return scored_links[0][2]

    def _score_package_link(self, link: SubtitleThreadPackageLink) -> int:
        text = html.unescape(f"{link.label} {link.filename_hint}").lower()
        suffix = Path((link.filename_hint or link.label or link.url).split("?")[0]).suffix.lower()
        score = 0
        if link.kind == "attachment":
            score += 100
        if link.is_direct_download:
            score += 40
        if suffix in SUBTITLE_EXTENSIONS:
            score += 35
        elif suffix in _ARCHIVE_SUFFIXES:
            score += 20
        if self._contains_any(text, _BATCH_KEYWORDS):
            score += 25
        if self._contains_any(text, _REVISION_KEYWORDS):
            score += 15
        if self._contains_any(text, _FONT_KEYWORDS):
            score -= 80
        if self._contains_any(text, _PATCH_KEYWORDS):
            score -= 40
        if self._contains_any(text, _SPECIAL_KEYWORDS):
            score -= 30
        return score

    def _infer_filename(
        self,
        candidate: SubtitleCandidate,
        download_url: str,
        package: Optional[SubtitleThreadPackage] = None,
    ) -> str:
        raw_name = Path(download_url.split("?")[0]).name
        suffix = Path(raw_name).suffix.lower()
        if suffix in _ARCHIVE_SUFFIXES or suffix in SUBTITLE_EXTENSIONS:
            return raw_name

        if package is not None:
            for link in package.links:
                if link.url != download_url:
                    continue
                hinted_name = Path((link.filename_hint or link.label).strip()).name
                hinted_suffix = Path(hinted_name).suffix.lower()
                if hinted_name and (
                    hinted_suffix in _ARCHIVE_SUFFIXES
                    or hinted_suffix in SUBTITLE_EXTENSIONS
                ):
                    return self._safe_filename(hinted_name, "subtitle_candidate.bin")

        safe_title = re.sub(r'[\\/:*?"<>|]+', '_', candidate.title).strip("._ ")
        return f"{safe_title or 'subtitle_candidate'}.bin"

    def _extract_attachment_filename(
        self,
        link_text: str,
        attachment_url: str,
    ) -> str:
        text = html.unescape(link_text or "").strip()
        if text:
            return text
        return Path(unquote(attachment_url.split("?")[0])).name

    def _is_downloadable_attachment(
        self,
        filename_hint: str,
        attachment_url: str,
    ) -> bool:
        filename = (filename_hint or "").strip().lower()
        suffix = Path(filename).suffix.lower()
        if suffix in _ARCHIVE_SUFFIXES or suffix in SUBTITLE_EXTENSIONS:
            return True
        if suffix in _IMAGE_SUFFIXES:
            return False
        fallback_suffix = Path(unquote(attachment_url.split("?")[0])).suffix.lower()
        if fallback_suffix in _IMAGE_SUFFIXES:
            return False
        return False

    @staticmethod
    def _safe_filename(name: str, fallback: str) -> str:
        safe_name = re.sub(r'[\\/:*?"<>|]+', '_', html.unescape(name or ""))
        safe_name = safe_name.strip("._ ")
        return safe_name or fallback

    def _resolve_download_filename(self, response: requests.Response, fallback: str) -> str:
        content_disposition = response.headers.get("Content-Disposition") or ""
        filename = fallback

        match_utf8 = re.search(
            r"filename\*=utf-8''([^;]+)",
            content_disposition,
            re.IGNORECASE,
        )
        if match_utf8:
            filename = unquote(match_utf8.group(1)).strip()
        else:
            match_basic = re.search(
                r'filename="?([^";]+)"?',
                content_disposition,
                re.IGNORECASE,
            )
            if match_basic:
                filename = unquote(match_basic.group(1)).strip()

        return self._safe_filename(filename, fallback)

    def _download_file(self, download_url: str, destination: Path) -> Path:
        headers = {
            "Referer": self.base_url,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            ),
        }
        with requests.get(
            download_url,
            headers=headers,
            timeout=self.timeout,
            stream=True,
        ) as response:
            response.raise_for_status()
            resolved_name = self._resolve_download_filename(response, destination.name)
            resolved_path = destination.with_name(resolved_name)
            with open(resolved_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
        return resolved_path

    def _unique(self, items: List[str]) -> List[str]:
        seen = set()
        result = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
