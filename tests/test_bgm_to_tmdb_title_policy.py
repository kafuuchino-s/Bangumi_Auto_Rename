from __future__ import annotations

from pathlib import Path

from src.rename.bgm_to_tmdb import BgmToTmdbBridgeToolState, build_tmdb_legal_graph
from src.rename.bgm_to_tmdb.models import BgmToTmdbInput
from src.rename.bgm_to_tmdb.title_policy import (
    normalize_title_language_order,
    resolve_output_title,
)
from src.rename.bgm_to_tmdb import title_policy


def test_normalize_title_language_order_deduplicates_and_keeps_auto_exclusive() -> None:
    assert normalize_title_language_order(
        ["zh-CN", "zh-CN", "not-a-language", "en-US"]
    ) == ["zh-CN", "en-US"]
    assert normalize_title_language_order(["auto", "zh-CN"]) == ["auto"]
    assert normalize_title_language_order([]) == ["auto"]


def test_resolve_output_title_uses_configured_order_and_metadata_fallbacks() -> None:
    localized = {"zh-CN": "女子落语", "en-US": "Joshiraku"}

    assert resolve_output_title(
        ["zh-CN", "en-US"],
        localized_titles=localized,
        original_title="じょしらく",
        current_title="Joshiraku",
    ) == "女子落语"
    assert resolve_output_title(
        ["en-US", "zh-CN"],
        localized_titles=localized,
        original_title="じょしらく",
        current_title="女子落语",
    ) == "Joshiraku"
    assert resolve_output_title(
        ["zh-CN"],
        localized_titles={},
        original_title="じょしらく",
        current_title="Joshiraku",
    ) == "じょしらく"
    assert resolve_output_title(
        ["auto"],
        localized_titles=localized,
        original_title="じょしらく",
        current_title="Joshiraku",
    ) == "Joshiraku"


class _TitlePolicyTmdbSearch:
    names = {
        "zh-CN": "女子落语",
        "zh-TW": "女子落語",
        "ja-JP": "じょしらく",
        "en-US": "Joshiraku",
    }

    def _tmdb_tv_info(self, tmdb_id: int, *, language: str = "en-US"):
        assert tmdb_id == 79040
        return {
            "id": tmdb_id,
            "name": self.names.get(language, ""),
            "original_name": "じょしらく",
            "first_air_date": "2012-07-06",
            "seasons": [],
        }

    def get_tv_info_by_id(self, tmdb_id: int):
        return self._tmdb_tv_info(tmdb_id, language="en-US")

    def enrich_tv_alias_metadata(self, tv_info):
        return tv_info


def test_bridge_applies_configured_title_to_hydrated_candidate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        title_policy.cm,
        "get_config",
        lambda key: ["zh-CN", "en-US"]
        if key == "rename_output_title_language_order"
        else "",
    )
    state = BgmToTmdbBridgeToolState(
        bridge_input=BgmToTmdbInput(source_path="Joshiraku (2012)"),
        legal_graph=build_tmdb_legal_graph([]),
        run_dir=tmp_path,
        tmdb_search=_TitlePolicyTmdbSearch(),
    )

    result = state.handle_tool(
        "get_tmdb_legal_graph",
        {"tmdb_refs": ["tv:79040"]},
    )

    assert result["ok"] is True
    assert state.legal_graph.candidates[0].display_title == "女子落语"
