from __future__ import annotations

from typing import Iterable


STRICT_RELATED_RELATION_KINDS: frozenset[str] = frozenset({
    '续集',
    '前传',
    '番外篇',
    '不同演绎',
    '演绎',
    '总集篇',
    '衍生',
    # English aliases used by tests and Case Agent request contracts.
    'sequel',
    'prequel',
    'side_story',
    'side story',
    'adaptation',
    'parent',
    'child',
    'special',
})

STRICT_RELATED_RELATION_KIND_KEYS: frozenset[str] = frozenset(
    relation.casefold() for relation in STRICT_RELATED_RELATION_KINDS
)


def normalize_relation_name(value: object) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    try:
        recovered = text.encode('latin1').decode('utf-8')
    except UnicodeError:
        return text
    if recovered and recovered != text and any('\u4e00' <= char <= '\u9fff' for char in recovered):
        return recovered.strip()
    return text


def is_strict_related_relation(value: object) -> bool:
    return normalize_relation_name(value).casefold() in STRICT_RELATED_RELATION_KIND_KEYS


def strict_requested_relation_keys(values: Iterable[object] | None) -> tuple[set[str], list[str]]:
    requested = [normalize_relation_name(value) for value in (values or [])]
    requested = [value for value in requested if value]
    if not requested:
        return set(STRICT_RELATED_RELATION_KIND_KEYS), []
    allowed = {
        value.casefold()
        for value in requested
        if value.casefold() in STRICT_RELATED_RELATION_KIND_KEYS
    }
    disallowed = [
        value
        for value in requested
        if value.casefold() not in STRICT_RELATED_RELATION_KIND_KEYS
    ]
    return allowed, disallowed
