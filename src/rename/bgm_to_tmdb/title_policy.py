from __future__ import annotations

from collections.abc import Mapping

from ...config.config_manager import (
    DEFAULT_TITLE_LANGUAGE_ORDER,
    TITLE_LANGUAGE_OPTIONS,
    cm,
)


def normalize_title_language_order(value: object) -> list[str]:
    """Return a de-duplicated, validated output-title preference list."""
    raw_values = value if isinstance(value, (list, tuple)) else [value]
    order: list[str] = []
    for raw in raw_values:
        language = str(raw or "").strip()
        if language not in TITLE_LANGUAGE_OPTIONS:
            continue
        if language == "auto":
            return ["auto"]
        if language not in order:
            order.append(language)
    return order or list(DEFAULT_TITLE_LANGUAGE_ORDER)


def configured_title_language_order() -> list[str]:
    return normalize_title_language_order(
        cm.get_config("rename_output_title_language_order")
    )


def resolve_output_title(
    order: object,
    *,
    localized_titles: Mapping[str, object],
    original_title: object,
    current_title: object,
) -> str:
    """Choose a display title without changing semantic candidate selection.

    ``auto`` deliberately keeps the title selected by the existing evidence
    alignment path. Explicit preferences get deterministic metadata fallbacks
    so a missing translation never produces an empty path component.
    """
    preferences = normalize_title_language_order(order)
    current = str(current_title or "").strip()
    if preferences == ["auto"]:
        return current

    effective_order = list(preferences)
    for fallback in ("original", "en-US"):
        if fallback not in effective_order:
            effective_order.append(fallback)

    for language in effective_order:
        if language == "original":
            title = str(original_title or "").strip()
        else:
            title = str(localized_titles.get(language) or "").strip()
        if title:
            return title
    return current


__all__ = [
    "configured_title_language_order",
    "normalize_title_language_order",
    "resolve_output_title",
]
