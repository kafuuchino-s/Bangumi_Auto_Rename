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
    assert "revision" in result.thread_packages[0].package_flags
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
        return destination

    monkeypatch.setattr(provider, "load_thread_packages", lambda candidate: candidate)
    monkeypatch.setattr(provider, "_download_file", fake_download)

    result = provider.download(candidate, tmp_path, package=package)

    assert result.status == "success"
    assert result.selected_package == package
    assert captured["url"].endswith("aid=42")
