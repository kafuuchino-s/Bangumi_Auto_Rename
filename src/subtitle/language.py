"""Subtitle language normalization and Chinese script evidence."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from charset_normalizer import from_bytes
from zhconv_rs import zhconv

LANGUAGE_MAP: dict[str, tuple[str, bool]] = {
    "chs": ("zh-CN", True),
    "sc": ("zh-CN", True),
    "gb": ("zh-CN", True),
    "简": ("zh-CN", True),
    "简体": ("zh-CN", True),
    "简中": ("zh-CN", True),
    "zh-hans": ("zh-CN", True),
    "zh-cn": ("zh-CN", True),
    "cn": ("zh-CN", True),
    "chinese": ("zh-CN", True),
    "cht": ("zh-TW", False),
    "tc": ("zh-TW", False),
    "big5": ("zh-TW", False),
    "繁": ("zh-TW", False),
    "繁体": ("zh-TW", False),
    "繁中": ("zh-TW", False),
    "zh-hant": ("zh-TW", False),
    "zh-tw": ("zh-TW", False),
    "tw": ("zh-TW", False),
    "zh-hk": ("zh-HK", False),
    "hk": ("zh-HK", False),
    "jp": ("ja", False),
    "jpn": ("ja", False),
    "ja": ("ja", False),
    "japanese": ("ja", False),
    "日": ("ja", False),
    "日语": ("ja", False),
    "en": ("en", False),
    "eng": ("en", False),
    "english": ("en", False),
    "ko": ("ko", False),
    "kor": ("ko", False),
    "korean": ("ko", False),
}

ChineseScript = Literal["simplified", "traditional", "unknown"]

_MAX_READ_BYTES = 4 * 1024 * 1024
_MAX_DIALOGUE_CHARS = 500_000
_MIN_VARIANT_EVIDENCE = 20
_MIN_DOMINANCE = 0.75
_HAN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_FOREIGN_SCRIPT_RE = re.compile(r"[\u3040-\u30ff\uac00-\ud7af]")
_ASS_TAG_RE = re.compile(r"\{[^{}]*\}")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TIMESTAMP_RE = re.compile(
    r"^\s*\d{1,2}:\d{2}(?::\d{2})?[,.]\d{2,3}\s*-->"
)
_MICRODVD_RE = re.compile(r"^\{\d+\}\{\d*\}")


@dataclass(frozen=True)
class ChineseScriptEvidence:
    script: ChineseScript = "unknown"
    simplified_count: int = 0
    traditional_count: int = 0


def normalize_language(lang: str | None) -> tuple[str, bool]:
    """Normalize a release language tag to Emby language metadata."""
    if not lang:
        return "zh", False
    normalized = lang.lower().strip()
    return LANGUAGE_MAP.get(normalized, (lang, False))


def known_language_code(lang: str | None) -> str | None:
    """Return normalized Emby code only for a recognized language tag."""
    if not lang:
        return None
    normalized = lang.lower().strip()
    value = LANGUAGE_MAP.get(normalized)
    return value[0] if value else None


def detect_chinese_script(path: Path) -> ChineseScriptEvidence:
    """Classify high-confidence Simplified/Traditional dialogue content.

    Only explicit one-character script variants count as evidence. Mixed,
    short, Japanese-heavy, unreadable, and binary subtitles stay unknown.
    """
    if path.suffix.lower() == ".idx" or not path.is_file():
        return ChineseScriptEvidence()

    try:
        with path.open("rb") as subtitle_file:
            payload = subtitle_file.read(_MAX_READ_BYTES)
    except OSError:
        return ChineseScriptEvidence()
    if not payload or payload.count(b"\x00") > len(payload) // 100:
        return ChineseScriptEvidence()

    try:
        match = from_bytes(payload).best()
        if match is None:
            return ChineseScriptEvidence()
        dialogue = _extract_dialogue(str(match), path.suffix.lower())
    except Exception:
        return ChineseScriptEvidence()
    if not dialogue:
        return ChineseScriptEvidence()

    han_counts = Counter(_HAN_RE.findall(dialogue))

    simplified = 0
    traditional = 0
    for character, count in han_counts.items():
        cn, tw = _script_variants(character)
        if len(cn) != 1 or len(tw) != 1 or cn == tw:
            continue
        if character == cn:
            simplified += count
        elif character == tw:
            traditional += count

    total = simplified + traditional
    evidence = ChineseScriptEvidence(
        simplified_count=simplified,
        traditional_count=traditional,
    )
    if _is_foreign_script_dominant(dialogue):
        return evidence
    if total < _MIN_VARIANT_EVIDENCE:
        return evidence
    dominance = abs(simplified - traditional) / total
    if dominance < _MIN_DOMINANCE:
        return evidence
    return ChineseScriptEvidence(
        script="simplified" if simplified > traditional else "traditional",
        simplified_count=simplified,
        traditional_count=traditional,
    )


def _extract_dialogue(text: str, suffix: str) -> str:
    lines: list[str] = []
    if suffix in {".ass", ".ssa"}:
        for line in text.splitlines():
            if line.lstrip().lower().startswith("dialogue:"):
                lines.append(line.split(",", 9)[-1])
    else:
        for line in text.splitlines():
            stripped = line.strip()
            if (
                not stripped
                or stripped.isdigit()
                or _TIMESTAMP_RE.match(stripped)
                or stripped.upper() == "WEBVTT"
            ):
                continue
            lines.append(_MICRODVD_RE.sub("", stripped))

    dialogue = "\n".join(lines)[:_MAX_DIALOGUE_CHARS]
    dialogue = _ASS_TAG_RE.sub("", dialogue)
    dialogue = _HTML_TAG_RE.sub("", dialogue)
    return re.sub(r"\\[Nnh]", " ", dialogue)


def _is_foreign_script_dominant(dialogue: str) -> bool:
    meaningful_lines = [
        line for line in dialogue.splitlines() if _HAN_RE.search(line)
    ]
    if not meaningful_lines:
        return False
    foreign_lines = sum(
        bool(_FOREIGN_SCRIPT_RE.search(line)) for line in meaningful_lines
    )
    return foreign_lines * 2 >= len(meaningful_lines)


@lru_cache(maxsize=4096)
def _script_variants(character: str) -> tuple[str, str]:
    return zhconv(character, "zh-cn"), zhconv(character, "zh-tw")
