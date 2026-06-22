from src.subtitle.providers import (
    ACGRIPProvider,
    SubtitleCandidate,
    SubtitleThreadPackage,
)


class FakeText(str):
    def getall(self):
        return [self]


class FakeSelectors(list):
    @property
    def first(self):
        return self[0] if self else None

    def getall(self):
        return [str(item) for item in self]


class FakeNode:
    def __init__(self, text="", attrib=None, children=None, selector_map=None, tag="div"):
        self._text = text
        self.attrib = attrib or {}
        self.children = children or []
        self.selector_map = selector_map or {}
        self.tag = tag
        self.parent = None
        for child in self.children:
            child.parent = self

    def css(self, selector):
        if selector == "::text":
            return FakeSelectors([FakeText(part) for part in self._text.split("\n") if part])
        if selector in self.selector_map:
            return FakeSelectors(self.selector_map.get(selector, []))
        if selector == "a":
            return FakeSelectors(self.children)
        return FakeSelectors([])

    def get_all_text(self, separator=" ", strip=False):
        parts = [self._text]
        for child in self.children:
            parts.append(child._text)
        text = separator.join(part for part in parts if part)
        return text.strip() if strip else text


def make_post(post_id, header_text, body_text, links, floor_text="推荐", author=None):
    anchor_links = []
    for href, text in links:
        anchor_links.append(FakeNode(text=text, attrib={"href": href}, tag="a"))

    header = FakeNode(text=header_text, selector_map={"a.xw1": [FakeNode(text=author or "")]} )
    floor = FakeNode(text=floor_text, attrib={"id": f"postnum{post_id.split('_')[-1]}"}, tag="a")
    body = FakeNode(text=body_text, children=anchor_links)

    return FakeNode(
        attrib={"id": post_id},
        selector_map={
            'a': anchor_links + [floor],
            'div.pi': [header],
            'td.t_f': [body],
            'div.pct': [body],
            'ignore_js_op': [body],
            'a[id^="postnum"]': [floor],
            'div.pi a.xw1': [FakeNode(text=author or "")],
            'div.authi a.xw1': [],
            'div.pls a.xi2': [FakeNode(text=author or "")],
            'div.pi em': [],
        },
    )


class FakePage(FakeNode):
    def __init__(self, posts, links=None):
        all_links = list(links or [])
        for post in posts:
            all_links.extend(post.css('a'))
        super().__init__(selector_map={
            'div[id^="post_"]': posts,
            'a': all_links,
        })


def test_load_thread_packages_collects_floor_context(monkeypatch):
    provider = ACGRIPProvider()
    candidate = SubtitleCandidate(
        title="Seitokai",
        detail_url="https://bbs.acgrip.com/forum.php?mod=viewthread&tid=202",
        source="acgrip",
    )

    page1 = FakePage(
        [
            make_post(
                "post_100",
                "sommio 发表于 2023-03-13 06:43:45",
                "修正版 简体全集 [HKG] Subtitle.7z",
                [
                    (
                        "forum.php?mod=attachment&aid=1",
                        "[HKG] Subtitle.7z",
                    )
                ],
                floor_text="推荐",
                author="sommio",
            )
        ],
        links=[FakeNode(text="2", attrib={"href": "forum.php?mod=viewthread&tid=202&page=2"}, tag="a")],
    )
    page2 = FakePage(
        [
            make_post(
                "post_101",
                "floater 发表于 2023-06-21 22:58:28",
                "补丁 单集 reinforce.zip",
                [("forum.php?mod=attachment&aid=2", "reinforce.zip")],
                floor_text="沙发",
                author="floater",
            )
        ]
    )

    calls = []

    def fake_fetch(url):
        calls.append(url)
        if "page=2" in url:
            return page2
        return page1

    monkeypatch.setattr(provider, "_fetch_page", fake_fetch)

    result = provider.load_thread_packages(candidate)

    assert result.pages_scanned == 2
    assert result.pagination_truncated is False
    assert len(result.thread_packages) == 2
    assert result.thread_packages[0].post_author == "sommio"
    assert "修正版" in result.thread_packages[0].post_text
    # package_flags 固定层不再检测（A1 删 _detect_package_flags，AI-first：
    # 包性质由 Pi 看 post_text + links 自判），恒为空 list
    assert result.thread_packages[0].package_flags == []
    assert result.thread_packages[0].links[0].url.endswith("aid=1")
    assert result.thread_packages[1].page_number == 2
    assert result.attachment_urls == [
        "https://bbs.acgrip.com/forum.php?mod=attachment&aid=1",
        "https://bbs.acgrip.com/forum.php?mod=attachment&aid=2",
    ]
    assert any("page=2" in url for url in calls)


def test_download_uses_selected_package(monkeypatch, tmp_path):
    provider = ACGRIPProvider()
    package = SubtitleThreadPackage(
        package_id="post-1",
        page_number=1,
        floor_label="第1楼",
        post_text="简体全集",
        context_text="简体全集",
        has_direct_download=True,
        package_flags=["batch", "simplified"],
        links=[],
    )
    package.links.append(
        provider._build_package_link(
            FakeNode(
                text="selected.zip",
                attrib={"href": "forum.php?mod=attachment&aid=42"},
                tag="a",
            )
        )
    )
    candidate = SubtitleCandidate(
        title="Seitokai",
        detail_url="https://bbs.acgrip.com/forum.php?mod=viewthread&tid=202",
        source="acgrip",
        thread_packages=[package],
        pages_scanned=1,
    )

    captured = {}

    def fake_download(url, destination):
        captured["url"] = url
        destination.write_text("data", encoding="utf-8")
        return destination, 1

    monkeypatch.setattr(provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(provider, "_download_file", fake_download)

    result = provider.download(candidate, tmp_path, package=package)

    assert result.status == "success"
    assert result.selected_package == package
    assert captured["url"].endswith("aid=42")


def test_is_downloadable_direct_url_classifies_links():
    """固定层硬筛：论坛直链放行，网盘外链拒绝，无后缀未知外链保守拒绝。"""
    p = ACGRIPProvider()
    # acgrip 论坛直链
    assert p._is_downloadable_direct_url(
        "https://bbs.acgrip.com/forum.php?mod=attachment&aid=abc"
    ) is True
    # 外部真直链（带压缩包后缀）
    assert p._is_downloadable_direct_url("https://example.com/subs.rar") is True
    assert p._is_downloadable_direct_url("https://example.com/ep01.ass") is True
    # 网盘外链（不可直接 GET）
    assert p._is_downloadable_direct_url("http://pan.baidu.com/s/1pJqs1zP") is False
    assert p._is_downloadable_direct_url("https://wwa.lanzoui.com/abc") is False
    assert p._is_downloadable_direct_url("https://www.alipan.com/s/xxx") is False
    # 无后缀未知外链（保守拒绝，逼 Pi 选论坛直链）
    assert p._is_downloadable_direct_url("https://example.com/somepage") is False
    # 空
    assert p._is_downloadable_direct_url("") is False


def test_download_rejects_cloud_disk_external_link(monkeypatch, tmp_path):
    """Pi 若传网盘外链（百度/蓝奏）应被硬筛拒绝，不发起下载。"""
    provider = ACGRIPProvider()
    package = SubtitleThreadPackage(
        package_id="post-1",
        page_number=1,
        floor_label="楼主",
        post_text="字幕",
        context_text="字幕",
        has_direct_download=True,
        package_flags=[],
        links=[],
    )
    candidate = SubtitleCandidate(
        title="Aria",
        detail_url="https://bbs.acgrip.com/forum.php?mod=viewthread&tid=346",
        source="acgrip",
        thread_packages=[package],
        pages_scanned=1,
    )

    called = {"download": False}

    def fake_download(url, destination):
        called["download"] = True
        return destination, 1

    monkeypatch.setattr(provider, "load_thread_packages", lambda c: c)
    monkeypatch.setattr(provider, "_download_file", fake_download)

    result = provider.download(
        candidate,
        tmp_path,
        package=package,
        download_url="http://pan.baidu.com/s/1pJqs1zP",
    )

    assert result.status == "no_download"
    assert "论坛直链" in (result.error or "")
    # 关键：不应真的去 GET 网盘页面
    assert called["download"] is False


def test_download_accepts_acgrip_direct_link(monkeypatch, tmp_path):
    """Pi 传 acgrip 论坛直链应放行下载（硬筛不误伤正经直链）。"""
    provider = ACGRIPProvider()
    package = SubtitleThreadPackage(
        package_id="post-1",
        page_number=1,
        floor_label="楼主",
        post_text="字幕",
        context_text="字幕",
        has_direct_download=True,
        package_flags=[],
        links=[],
    )
    candidate = SubtitleCandidate(
        title="Aria",
        detail_url="https://bbs.acgrip.com/forum.php?mod=viewthread&tid=346",
        source="acgrip",
        thread_packages=[package],
        pages_scanned=1,
    )

    captured = {}

    def fake_download(url, destination):
        captured["url"] = url
        destination.write_text("data", encoding="utf-8")
        return destination, 1

    monkeypatch.setattr(provider, "load_thread_packages", lambda c: c)
    monkeypatch.setattr(provider, "_download_file", fake_download)

    direct = "https://bbs.acgrip.com/forum.php?mod=attachment&aid=NDAyfDliZWM2"
    result = provider.download(
        candidate, tmp_path, package=package, download_url=direct
    )

    assert result.status == "success"
    assert captured["url"] == direct


# ---------------------------------------------------------------------------
# 下载网络瞬时错误重试
# ---------------------------------------------------------------------------

def test_download_retries_on_transient_ssl_then_succeeds(monkeypatch, tmp_path):
    """SSL 握手失败前 N-1 次、第 N 次成功 → 重试恢复，download_attempts=N。"""
    import requests as _requests
    import src.subtitle.providers.acgrip as acg_mod
    monkeypatch.setattr(acg_mod, "_DOWNLOAD_RETRY_BACKOFF_SECONDS", 0.0)

    provider = ACGRIPProvider()
    candidate = SubtitleCandidate(
        title="Aria",
        detail_url="https://bbs.acgrip.com/forum.php?mod=viewthread&tid=346",
        source="acgrip",
    )
    package = SubtitleThreadPackage(
        package_id="p", page_number=1, floor_label="楼主",
        post_text="字幕", context_text="", has_direct_download=True,
        package_flags=[], links=[],
    )

    call_count = {"n": 0}

    class FakeResp:
        status_code = 200
        headers = {"Content-Disposition": ""}

        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=8192):
            yield b"subtitledata"

    def fake_get(url, headers, timeout, stream):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise _requests.exceptions.SSLError(
                "[SSL: WRONG_VERSION_NUMBER] wrong version number"
            )
        return FakeResp()

    monkeypatch.setattr(acg_mod.requests, "get", fake_get)
    monkeypatch.setattr(provider, "load_thread_packages", lambda c: c)

    direct = "https://bbs.acgrip.com/forum.php?mod=attachment&aid=abc"
    result = provider.download(
        candidate, tmp_path, package=package, download_url=direct
    )

    assert result.status == "success"
    assert result.download_attempts == 3
    assert call_count["n"] == 3


def test_download_exhausts_retry_on_persistent_ssl(monkeypatch, tmp_path):
    """SSL 持续失败 → 重试耗尽，status=failed，download_attempts=MAX。"""
    import requests as _requests
    import src.subtitle.providers.acgrip as acg_mod
    monkeypatch.setattr(acg_mod, "_DOWNLOAD_RETRY_BACKOFF_SECONDS", 0.0)

    provider = ACGRIPProvider()
    candidate = SubtitleCandidate(
        title="Aria",
        detail_url="https://bbs.acgrip.com/forum.php?mod=viewthread&tid=346",
        source="acgrip",
    )
    package = SubtitleThreadPackage(
        package_id="p", page_number=1, floor_label="楼主",
        post_text="字幕", context_text="", has_direct_download=True,
        package_flags=[], links=[],
    )

    def fake_get(url, headers, timeout, stream):
        raise _requests.exceptions.SSLError("[SSL: WRONG_VERSION_NUMBER]")

    monkeypatch.setattr(acg_mod.requests, "get", fake_get)
    monkeypatch.setattr(provider, "load_thread_packages", lambda c: c)

    direct = "https://bbs.acgrip.com/forum.php?mod=attachment&aid=abc"
    result = provider.download(
        candidate, tmp_path, package=package, download_url=direct
    )

    assert result.status == "failed"
    assert result.download_attempts == acg_mod._DOWNLOAD_MAX_ATTEMPTS
    assert "SSL" in (result.error or "") or "ssl" in (result.error or "").lower()


def test_download_no_retry_on_4xx_permanent_error(monkeypatch, tmp_path):
    """4xx 永久错误（404）不重试，立即失败，download_attempts=1。"""
    import requests as _requests
    import src.subtitle.providers.acgrip as acg_mod
    monkeypatch.setattr(acg_mod, "_DOWNLOAD_RETRY_BACKOFF_SECONDS", 0.0)

    provider = ACGRIPProvider()
    candidate = SubtitleCandidate(
        title="Aria",
        detail_url="https://bbs.acgrip.com/forum.php?mod=viewthread&tid=346",
        source="acgrip",
    )
    package = SubtitleThreadPackage(
        package_id="p", page_number=1, floor_label="楼主",
        post_text="字幕", context_text="", has_direct_download=True,
        package_flags=[], links=[],
    )

    call_count = {"n": 0}

    def fake_get(url, headers, timeout, stream):
        call_count["n"] += 1
        resp = _requests.Response()
        resp.status_code = 404
        raise _requests.exceptions.HTTPError("404 Not Found", response=resp)

    monkeypatch.setattr(acg_mod.requests, "get", fake_get)
    monkeypatch.setattr(provider, "load_thread_packages", lambda c: c)

    direct = "https://bbs.acgrip.com/forum.php?mod=attachment&aid=abc"
    result = provider.download(
        candidate, tmp_path, package=package, download_url=direct
    )

    assert result.status == "failed"
    assert result.download_attempts == 1
    # 关键：4xx 不重试，只调用一次
    assert call_count["n"] == 1


def test_download_retries_on_5xx_server_error(monkeypatch, tmp_path):
    """5xx 服务端错误可重试（与 4xx 永久错误区分）。"""
    import requests as _requests
    import src.subtitle.providers.acgrip as acg_mod
    monkeypatch.setattr(acg_mod, "_DOWNLOAD_RETRY_BACKOFF_SECONDS", 0.0)

    provider = ACGRIPProvider()
    candidate = SubtitleCandidate(
        title="Aria",
        detail_url="https://bbs.acgrip.com/forum.php?mod=viewthread&tid=346",
        source="acgrip",
    )
    package = SubtitleThreadPackage(
        package_id="p", page_number=1, floor_label="楼主",
        post_text="字幕", context_text="", has_direct_download=True,
        package_flags=[], links=[],
    )

    call_count = {"n": 0}

    class FakeResp:
        status_code = 200
        headers = {"Content-Disposition": ""}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=8192):
            yield b"data"

    def fake_get(url, headers, timeout, stream):
        call_count["n"] += 1
        if call_count["n"] < 2:
            resp = _requests.Response()
            resp.status_code = 503
            raise _requests.exceptions.HTTPError("503 Service Unavailable", response=resp)
        return FakeResp()

    monkeypatch.setattr(acg_mod.requests, "get", fake_get)
    monkeypatch.setattr(provider, "load_thread_packages", lambda c: c)

    direct = "https://bbs.acgrip.com/forum.php?mod=attachment&aid=abc"
    result = provider.download(
        candidate, tmp_path, package=package, download_url=direct
    )

    assert result.status == "success"
    assert result.download_attempts == 2
    assert call_count["n"] == 2
