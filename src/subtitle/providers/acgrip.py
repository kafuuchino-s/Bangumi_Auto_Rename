from __future__ import annotations

import html
import re
import threading
import time
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
# 包性质关键词检测已删（AI-first）：_BATCH/_REVISION/_PATCH/_SPECIAL/_FONT/
# _SIMPLIFIED/_TRADITIONAL/_BILINGUAL 关键词表是硬编码死的，遇日文"フォント"等漏判，
# 且 special 把 ova/oad/sp 全归一类但 OAD 可能是正片需要的（0045 Gundam OVA 缺字幕）。
# 包性质（字幕/字体/special/简繁/batch）改由 Pi 看 post_text + links[].label/
# filename_hint 自判，固定层只给原文事实。package_flags 字段保留空 list 兼容。
_MAX_THREAD_PAGES = 3

# 下载网络瞬时错误重试：acgrip 偶发 SSL 握手失败（[SSL: WRONG_VERSION_NUMBER]）、
# 连接重置、超时。这类是网络抖动非业务失败，重试可恢复（0042 sel#2、0045 都踩过）。
# HTTP 4xx/5xx 中 4xx 是永久拒绝不重试；5xx 是服务端错误可重试。
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_RETRY_BACKOFF_SECONDS = 2.0


class DownloadRetryExhausted(Exception):
    """下载重试耗尽。携带实际尝试次数 attempts 供上层审计。"""

    def __init__(self, message: str, attempts: int, cause: Optional[Exception] = None):
        super().__init__(message)
        self.attempts = attempts
        self.__cause__ = cause


class ACGRIPProvider(SubtitleProvider):
    """基于 Scrapling 的 ACGRIP 字幕源 provider。"""

    provider_id = "acgrip"

    def __init__(self) -> None:
        self.base_url = cm.get_config("subtitle_auto_fetch_acgrip_base_url") or (
            "https://bbs.acgrip.com"
        )
        self.timeout = int(cm.get_config("subtitle_auto_fetch_timeout_seconds") or 30)
        self.browser_enabled = bool(
            cm.get_config("subtitle_auto_fetch_browser_enabled")
        )
        # 长驻 FetcherSession 复用连接（per-thread）。旧实现每次 _fetch_page 都
        # `with FetcherSession(...) as s: s.get()` 用完销毁，每次重新 TLS 握手 12-16s
        # （impersonate=chrome 握手包重 + acgrip TLS 协商慢）。复用后同线程第2次起
        # 0.75s（实测 18x）。thread-local 隔离：下载并发（ThreadPoolExecutor）时各
        # 线程独立 session 互不干扰，同线程内多次 _fetch_page 复用连接。
        # browser_enabled=True 时走 DynamicFetcher（无状态，不复用）。
        self._thread_local = threading.local()

    def _get_session(self):
        """获取当前线程的长驻 FetcherSession client（懒加载 + 复用连接）。

        browser_enabled=True 时返回 None（调用方走 DynamicFetcher 无状态分支）。
        FetcherSession 的 `.get` 在 `__enter__()` 返回的 client 上（_SyncSessionLogic），
        不是 FetcherSession 实例本身，所以存 enter 后的 client 并保持不退出。
        """
        if self.browser_enabled or FetcherSession is None:
            return None
        client = getattr(self._thread_local, "client", None)
        if client is None:
            session = FetcherSession(impersonate="chrome")
            client = session.__enter__()
            # 记住 session 以便析构时 __exit__（client 本身无 close）
            self._thread_local.client = client
            self._thread_local.session = session
        return client

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
        download_url: Optional[str] = None,
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

        # 附件选择交给 Pi（AI-first）：Pi 通过 submit_package(link_url=...) 指定具体
        # 附件下载。固定层不打分选"最好的"附件——那属于语义判断（哪个是正片/前篇/
        # 後篇/简繁），应由 Pi 据 link label/filename + post_text 决定。Pi 未指定
        # url 时回退取第一个可下载附件（兼容旧调用 + 单附件包无需 Pi 指定）。
        resolved_url = download_url or self._first_downloadable_url(
            candidate, selected_package
        )
        if not resolved_url:
            return SubtitleDownloadResult(
                candidate=candidate,
                downloaded_path=None,
                download_url=None,
                status="no_download",
                error="未找到可直接下载的附件链接",
                selected_package=selected_package,
            )

        # 固定层硬筛：只收论坛直链（acgrip attachment）或已知字幕/压缩后缀的直链。
        # 网盘外链（百度/蓝奏/123pan/阿里云盘等）无法直接 HTTP 下载（需登录/提取码/
        # 浏览器交互），且 Pi 不应把网盘外链当可下载附件提交。这里显式拦截，避免
        # 下载器去 GET 网盘页面拿到 HTML 当"下载成功"。100% 确定的事实判断（URL host）。
        if not self._is_downloadable_direct_url(resolved_url):
            logger.warning(
                f"[字幕抓取][ACGRIP] 拒绝非论坛直链（网盘外链不可直接下载）: "
                f"{resolved_url[:80]}"
            )
            return SubtitleDownloadResult(
                candidate=candidate,
                downloaded_path=None,
                download_url=resolved_url,
                status="no_download",
                error="链接非论坛直链（网盘外链不可直接下载），请选 acgrip 附件直链",
                selected_package=selected_package,
            )

        filename = self._infer_filename(candidate, resolved_url, selected_package)
        downloaded_path = destination_dir / filename

        attempts = 1
        try:
            downloaded_path, attempts = self._download_file(resolved_url, downloaded_path)
        except DownloadRetryExhausted as exc:
            logger.error(
                f"[字幕抓取][ACGRIP] 下载失败（重试 {exc.attempts}/{_DOWNLOAD_MAX_ATTEMPTS} 次后仍失败）: {exc.__cause__}"
            )
            return SubtitleDownloadResult(
                candidate=candidate,
                downloaded_path=None,
                download_url=resolved_url,
                status="failed",
                error=str(exc.__cause__) if exc.__cause__ else str(exc),
                selected_package=selected_package,
                download_attempts=exc.attempts,
            )
        except Exception as exc:
            # 4xx 永久错误等不重试直接抛出的情况（attempts=1）
            logger.error(f"[字幕抓取][ACGRIP] 下载失败（永久错误，未重试）: {exc}")
            return SubtitleDownloadResult(
                candidate=candidate,
                downloaded_path=None,
                download_url=resolved_url,
                status="failed",
                error=str(exc),
                selected_package=selected_package,
                download_attempts=1,
            )

        retry_note = f"（重试 {attempts - 1} 次后成功）" if attempts > 1 else ""
        logger.info(f"[字幕抓取][ACGRIP] 下载完成{retry_note}: {downloaded_path}")
        return SubtitleDownloadResult(
            candidate=candidate,
            downloaded_path=downloaded_path,
            download_url=resolved_url,
            status="success",
            selected_package=selected_package,
            download_attempts=attempts,
        )

    def _fetch_page(self, url: str):
        if DynamicFetcher is None or FetcherSession is None:
            raise RuntimeError("未安装 scrapling，请先安装该依赖")

        if self.browser_enabled:
            logger.info(f"[字幕抓取][ACGRIP] 动态抓取: {url}")
            return DynamicFetcher.fetch(url)

        # 长驻 FetcherSession 复用连接（per-thread）。首次 TLS 握手 ~13s，同线程
        # 后续请求复用连接 ~0.75s（18x）。旧实现每次 with FetcherSession 新建销毁，
        # 每次握手 12-16s 是 acgrip 慢的主因（不是网站慢，是握手成本每次重付）。
        session = self._get_session()
        return session.get(url, stealthy_headers=True)

    def close(self) -> None:
        """清理当前线程的长驻 FetcherSession（可选，进程退出时 OS 自动回收）。

        长进程场景下可主动调 close 释放当前线程的 session 连接。
        """
        session = getattr(self._thread_local, "session", None)
        if session is not None:
            try:
                session.__exit__(None, None, None)
            except Exception:
                pass
            self._thread_local.client = None
            self._thread_local.session = None

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
        # package_flags 不再固定层检测（AI-first）：Pi 看 post_text + links 自判包性质。

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
            package_flags=[],
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
            if any(link.is_direct_download and link.url for link in package.links):
                return package
        return None

    def _first_downloadable_url(
        self,
        candidate: SubtitleCandidate,
        package: Optional[SubtitleThreadPackage] = None,
    ) -> Optional[str]:
        # Pi 未指定具体附件时的兜底：取第一个可下载附件 url。固定层不做"哪个附件
        # 最好"的语义判断（不打分），交给 Pi 通过 submit_package(link_url=...) 选。
        if package is not None:
            for link in package.links:
                if link.is_direct_download and link.url:
                    return link.url

        if candidate.attachment_urls:
            return candidate.attachment_urls[0]

        for url in candidate.external_urls:
            suffix = Path(url.split("?")[0]).suffix.lower()
            if suffix in _ARCHIVE_SUFFIXES or suffix in SUBTITLE_EXTENSIONS:
                return url
        return None

    @staticmethod
    def _is_downloadable_direct_url(url: str) -> bool:
        """固定层硬筛：url 是否可直接 HTTP 下载（非网盘外链）。

        100% 确定的事实判断（URL host / 后缀模式），无语义：
        - acgrip 论坛附件直链（`attachment&aid=`）→ True
        - 非 acgrip 但 url 路径后缀是压缩包/字幕 → True（其它站点的真直链）
        - 网盘 host（百度/蓝奏/123pan/阿里云盘等）→ False（需登录/提取码，不可直接 GET）
        - 其它未知外链 → False（保守拒绝，逼 Pi 选论坛直链）
        """
        if not url:
            return False
        if _ATTACHMENT_RE.search(url):
            return True
        url_lower = url.lower()
        if any(host in url_lower for host in _CLOUD_HOST_KEYWORDS):
            return False
        suffix = Path(unquote(url.split("?")[0])).suffix.lower()
        if suffix in _ARCHIVE_SUFFIXES or suffix in SUBTITLE_EXTENSIONS:
            return True
        return False

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

    def _download_file(self, download_url: str, destination: Path) -> Tuple[Path, int]:
        """下载文件，对网络瞬时错误（SSL/连接/超时/5xx）重试。

        返回 (resolved_path, attempts)。attempts = 实际尝试次数（含重试）。
        永久错误（4xx、硬筛拒绝）不重试，直接抛出。
        """
        headers = {
            "Referer": self.base_url,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            ),
        }
        last_exc: Optional[Exception] = None
        for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
            try:
                with requests.get(
                    download_url,
                    headers=headers,
                    timeout=self.timeout,
                    stream=True,
                ) as response:
                    response.raise_for_status()
                    resolved_name = self._resolve_download_filename(
                        response, destination.name
                    )
                    resolved_path = destination.with_name(resolved_name)
                    with open(resolved_path, "wb") as file:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                file.write(chunk)
                return resolved_path, attempt
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else 0
                # 4xx 是永久拒绝（403/404/410），重试无意义 → 直接抛
                if 400 <= status_code < 500:
                    raise
                # 5xx 服务端错误 → 可重试
                last_exc = exc
                logger.warning(
                    f"[字幕抓取][ACGRIP] 下载 HTTP {status_code}（第 {attempt}/"
                    f"{_DOWNLOAD_MAX_ATTEMPTS} 次），重试: {exc}"
                )
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
                # 网络瞬时错误（SSL 握手失败/连接重置/超时/分块中断）→ 重试
                last_exc = exc
                logger.warning(
                    f"[字幕抓取][ACGRIP] 下载网络瞬时错误（第 {attempt}/"
                    f"{_DOWNLOAD_MAX_ATTEMPTS} 次），重试: {exc}"
                )
            if attempt < _DOWNLOAD_MAX_ATTEMPTS:
                time.sleep(_DOWNLOAD_RETRY_BACKOFF_SECONDS)
        # 重试耗尽，抛带 attempts 的异常供上层审计（attempt 此时 = MAX）
        assert last_exc is not None
        raise DownloadRetryExhausted(
            f"下载重试 {attempt} 次后仍失败: {last_exc}",
            attempts=attempt,
            cause=last_exc,
        )

    def _unique(self, items: List[str]) -> List[str]:
        seen = set()
        result = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
