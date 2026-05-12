from src.rename.metadata_resolver import (
    collect_movie_metadata_alias_titles,
    collect_tv_metadata_alias_titles,
    normalize_metadata_title,
)


def test_collect_tv_metadata_alias_titles_uses_only_supplied_metadata():
    titles = collect_tv_metadata_alias_titles(
        {"name": "Localized", "original_name": "Original"},
        alternative_titles={
            "results": [
                {"title": "Alternative"},
                {"name": "Alt Name"},
            ]
        },
        translations={
            "translations": [
                {"data": {"name": "Translated"}},
                {"data": {"title": "Translated Title"}},
            ]
        },
    )

    assert titles == [
        "Localized",
        "Original",
        "Alternative",
        "Alt Name",
        "Translated",
        "Translated Title",
    ]


def test_collect_tv_metadata_alias_titles_deduplicates_by_normalized_title():
    titles = collect_tv_metadata_alias_titles(
        {"name": "Example Title", "original_name": "Example-Title"},
        alternative_titles={"results": [{"title": "Example.Title"}]},
        translations={"translations": []},
    )

    assert titles == ["Example Title"]


def test_collect_movie_metadata_alias_titles_uses_alternatives_and_translations():
    titles = collect_movie_metadata_alias_titles(
        {"title": "Short Peace", "original_title": "SHORT PEACE"},
        alternative_titles={"titles": [{"title": "GAMBO"}]},
        translations={"translations": [{"data": {"title": "九十九"}}]},
    )

    assert titles == ["Short Peace", "GAMBO", "九十九"]


def test_normalize_metadata_title_matches_score_normalization_shape():
    assert normalize_metadata_title("Example：Title/OVA") == "example title ova"
