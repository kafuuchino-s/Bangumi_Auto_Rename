"""auto_fetch 固定层并发加速 + load 缓存单测（架构改进 A+B+C）。

验证 pi_tools 内部并行/缓存机制（不改 Pi tool schema 语义，只改批内执行方式）：

- A: search_candidates 多关键词并发 search（实测 acgrip 4.9x 加速）。
- B: load_candidate_packages 同 candidate_ref 二次 load 缓存命中（合帖多 subject
  复用，如 0042 ARIA tid=3582 覆盖 3 subject，省重复 HTTP）。
- C: load_candidate_packages 多 candidate_ref 并发 load。

不真起 Pi sidecar / 不发真实 HTTP：用 _FakeProvider 记录调用次数，验证并发/缓存行为。
"""
from __future__ import annotations

import pytest

from src.subtitle.auto_fetch_case_agent import (
    AutoFetchCaseToolState,
    MissingVideoCard,
    ScanScopeCard,
    SearchKeywordCard,
)
from src.subtitle.auto_fetch_case_agent.workspace import (
    build_auto_fetch_case_workspace,
)
from src.subtitle.providers import SubtitleCandidate, SubtitleThreadPackage

# 复用 pi_runner 测试的 helper
from tests.test_auto_fetch_case_agent_pi_runner import (
    _candidate,
    _make_package,
    _workspace,
    _FakeProvider,
)


def test_search_candidates_concurrent_multi_keyword(tmp_path):
    """A: search_candidates 传多关键词，全部被并发搜索 + 候选都注入 workspace。"""
    cands_a = [_candidate("alpha-字幕", packages=[_make_package("pa", ["batch", "simplified"])])]
    cands_b = [_candidate("beta-字幕", packages=[_make_package("pb", ["batch", "simplified"])])]
    cands_c = [_candidate("gamma-字幕", packages=[_make_package("pc", ["batch", "simplified"])])]
    provider = _FakeProvider({"alpha": cands_a, "beta": cands_b, "gamma": cands_c})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)

    result = state.handle_tool(
        "search_candidates", {"keywords": ["alpha", "beta", "gamma"]}
    )
    assert result["ok"] is True
    # 3 个词都被搜（并发不丢词）
    assert sorted(provider.search_calls) == ["alpha", "beta", "gamma"]
    # 3 个候选都注入 workspace（CD1/CD2/CD3）
    assert result["candidate_count"] == 3
    assert len(result["new_candidate_refs"]) == 3
    # per_keyword 每个词各 1 候选
    pk = {p["keyword"]: p["candidate_count"] for p in result["per_keyword"]}
    assert pk == {"alpha": 1, "beta": 1, "gamma": 1}


def test_search_candidates_concurrent_dedup_same_thread(tmp_path):
    """A 并发后去重仍正确：两个词命中同帖（detail_url 相同）只注入一次。"""
    same_cand = _candidate("shared-thread", packages=[_make_package("ps", ["batch", "simplified"])])
    provider = _FakeProvider({"word1": [same_cand], "word2": [same_cand]})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)

    result = state.handle_tool("search_candidates", {"keywords": ["word1", "word2"]})
    # 两词都搜了
    assert sorted(provider.search_calls) == ["word1", "word2"]
    # 但同帖 detail_url 去重 → 只 1 个候选
    assert result["candidate_count"] == 1


def test_load_candidate_packages_cache_hit_skips_http(tmp_path):
    """B: 同 candidate_ref 二次 load 缓存命中，provider.load_thread_packages 不重复调用。"""
    cands = [_candidate("Foo 字幕", packages=[_make_package("p1", ["batch", "simplified"])])]
    provider = _FakeProvider({"Foo": cands})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})

    # 第一次 load：调 provider.load_thread_packages 1 次
    r1 = state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    assert r1["ok"] is True
    assert r1["status"] == "packages_loaded"
    assert len(provider.load_calls) == 1
    assert r1["per_candidate"][0].get("cached") is not True

    # 第二次 load 同 ref：缓存命中，不重复调 provider
    r2 = state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    assert r2["ok"] is True
    assert len(provider.load_calls) == 1  # 仍 1 次，没增加
    assert r2["per_candidate"][0].get("cached") is True
    # 包 ref 仍返回（复用 workspace 已有）
    assert len(r2["per_candidate"][0]["package_refs"]) == 1


def test_load_candidate_packages_concurrent_multi_ref(tmp_path):
    """C: load_candidate_packages 传多 ref，全部被并发 load（都调 provider）。"""
    cands = [
        _candidate(f"c{i}", packages=[_make_package(f"p{i}", ["batch", "simplified"])])
        for i in range(3)
    ]
    provider = _FakeProvider({"Foo": cands})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})

    result = state.handle_tool(
        "load_candidate_packages", {"candidate_refs": ["CD1", "CD2", "CD3"]}
    )
    assert result["ok"] is True
    # 3 个 ref 都被 load（并发不漏）
    assert len(provider.load_calls) == 3
    assert len(result["candidate_refs"]) == 3
    # 每个 ref 各分到包
    assert len(result["package_refs"]) == 3


def test_load_candidate_packages_partial_cache_partial_load(tmp_path):
    """B+C 混合：部分 ref 已缓存 + 部分新 ref，缓存的不调 provider、新的并发 load。"""
    cands = [
        _candidate(f"c{i}", packages=[_make_package(f"p{i}", ["batch", "simplified"])])
        for i in range(3)
    ]
    provider = _FakeProvider({"Foo": cands})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})
    # 先 load CD1（缓存它）
    state.handle_tool("load_candidate_packages", {"candidate_ref": "CD1"})
    assert len(provider.load_calls) == 1

    # 再 load CD1（缓存）+ CD2 + CD3（新）
    result = state.handle_tool(
        "load_candidate_packages", {"candidate_refs": ["CD1", "CD2", "CD3"]}
    )
    assert result["ok"] is True
    # 只 CD2/CD3 新调 provider（CD1 缓存），load_calls 从 1 → 3
    assert len(provider.load_calls) == 3
    # per_candidate 标记 cached
    by_ref = {p["candidate_ref"]: p for p in result["per_candidate"]}
    assert by_ref["CD1"].get("cached") is True
    assert by_ref["CD2"].get("cached") is not True
    assert by_ref["CD3"].get("cached") is not True


def test_load_candidate_packages_concurrency_limit_respected(tmp_path):
    """C 并发上限 _LOAD_CONCURRENCY：超过上限的 ref 仍全部 load（并发只是上限，
    不是丢弃，分批 limit _LOAD_CANDIDATE_BATCH_LIMIT 才控总量）。"""
    from src.subtitle.auto_fetch_case_agent.pi_tools import (
        _LOAD_CANDIDATE_BATCH_LIMIT,
    )
    # 用刚好 batch limit 个 ref（3），都在并发上限内
    cands = [
        _candidate(f"c{i}", packages=[_make_package(f"p{i}", ["batch", "simplified"])])
        for i in range(_LOAD_CANDIDATE_BATCH_LIMIT)
    ]
    provider = _FakeProvider({"Foo": cands})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)
    state.handle_tool("search_candidates", {"keyword": "Foo"})

    refs = [f"CD{i+1}" for i in range(_LOAD_CANDIDATE_BATCH_LIMIT)]
    result = state.handle_tool("load_candidate_packages", {"candidate_refs": refs})
    assert len(result["candidate_refs"]) == _LOAD_CANDIDATE_BATCH_LIMIT
    assert len(provider.load_calls) == _LOAD_CANDIDATE_BATCH_LIMIT


class _EmptyThenHitProvider(_FakeProvider):
    """首调返空、重试命中的 fake（模拟 acgrip 偶发空结果）。

    指定 keyword 的前 N 次调用返回 []，之后返回真实候选。
    """

    def __init__(self, candidates_by_keyword, empty_first: dict[str, int] | None = None):
        super().__init__(candidates_by_keyword)
        self._empty_first = empty_first or {}
        self._call_count: dict[str, int] = {}

    def search(self, keyword, limit=10):
        self.search_calls.append(keyword)
        self._call_count[keyword] = self._call_count.get(keyword, 0) + 1
        n = self._empty_first.get(keyword, 0)
        if self._call_count[keyword] <= n:
            return []  # 前 n 次偶发空
        return list(self._by_kw.get(keyword, []))


def test_search_candidates_retries_intermittent_empty(tmp_path):
    """偶发空结果重试：acgrip search.php 偶发 200 但空结果页（同词串行重试立即
    命中）。固定层对并发轮返 0 命中的词串行重试 1 次，补回被服务端瞬时状态吞掉
    的真实命中（0062 圣战的预兆曾因偶发 no_candidates 整季漏覆盖）。"""
    hit = _candidate("圣战的预兆-字幕", packages=[_make_package("pa", ["batch", "simplified"])])
    # 圣战的预兆：首调返空（偶发），重试命中
    provider = _EmptyThenHitProvider({"圣战的预兆": [hit]}, empty_first={"圣战的预兆": 1})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)

    result = state.handle_tool("search_candidates", {"keywords": ["圣战的预兆"]})
    assert result["ok"] is True
    # 重试后命中，不再是 no_candidates
    assert result["candidate_count"] == 1
    assert result["status"] == "candidates_loaded"
    # 该词被调用 2 次（并发轮 1 次 + 重试 1 次）
    assert provider.search_calls.count("圣战的预兆") == 2


def test_search_candidates_no_retry_when_already_hit(tmp_path):
    """已命中的词不重试（重试只针对并发轮返 0/异常的词），避免多余 HTTP。"""
    hit = _candidate("alpha-字幕", packages=[_make_package("pa", ["batch", "simplified"])])
    provider = _FakeProvider({"alpha": [hit], "beta": []})
    state = AutoFetchCaseToolState(workspace=_workspace(), run_dir=tmp_path, provider=provider)

    result = state.handle_tool("search_candidates", {"keywords": ["alpha", "beta"]})
    assert result["ok"] is True
    # alpha 命中只调 1 次（不重试）；beta 真无帖重试 1 次仍空
    assert provider.search_calls.count("alpha") == 1
    assert provider.search_calls.count("beta") == 2  # 并发 1 + 重试 1
