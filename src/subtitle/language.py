"""Subtitle language normalization and Chinese script evidence."""

from __future__ import annotations

import os
import re
import tempfile
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
_MAX_CONVERSION_BYTES = 16 * 1024 * 1024
_ASS_TAG_RE = re.compile(r"\{[^{}]*\}")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PROTECTED_TAG_RE = re.compile(r"(\{[^{}]*\}|<[^>]+>)")
_ASS_BREAK_RE = re.compile(r"(\\[Nnh])")
_ASS_DRAWING_RE = re.compile(r"\\p(?:[1-9]\d*)", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(
    r"^\s*\d{1,2}:\d{2}(?::\d{2})?[,.]\d{2,3}\s*-->"
)
_MICRODVD_RE = re.compile(r"^\{\d+\}\{\d*\}")


@dataclass(frozen=True)
class ChineseScriptEvidence:
    script: ChineseScript = "unknown"
    simplified_count: int = 0
    traditional_count: int = 0


@dataclass(frozen=True)
class ChineseSubtitleConversionResult:
    source_evidence: ChineseScriptEvidence
    output_evidence: ChineseScriptEvidence
    dialogue_line_count: int
    changed_dialogue_line_count: int


def convert_traditional_subtitle_to_simplified(
    source: Path,
    destination: Path,
) -> ChineseSubtitleConversionResult:
    """Convert confirmed Traditional dialogue while preserving structure."""
    if source.suffix.lower() not in {".ass", ".ssa", ".srt", ".vtt"}:
        raise ValueError("unsupported_subtitle_format")
    if destination.exists():
        raise FileExistsError(destination)

    source_evidence = detect_chinese_script(source)
    if source_evidence.script != "traditional":
        raise ValueError("source_not_high_confidence_traditional")

    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise ValueError("source_read_failed") from exc
    if len(payload) > _MAX_CONVERSION_BYTES:
        raise ValueError("subtitle_too_large")
    if not payload or payload.count(b"\x00") > len(payload) // 100:
        raise ValueError("binary_subtitle_not_supported")

    match = from_bytes(payload).best()
    if match is None:
        raise ValueError("subtitle_encoding_unknown")
    converted, dialogue_count, changed_count = _convert_subtitle_dialogue(
        str(match),
        source.suffix.lower(),
    )
    if not dialogue_count or not changed_count:
        raise ValueError("no_convertible_traditional_dialogue")

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=destination.suffix,
        dir=str(destination.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
            output.write(converted)
            output.flush()
            os.fsync(output.fileno())
        output_evidence = detect_chinese_script(temp_path)
        if output_evidence.script != "simplified":
            raise ValueError(
                "converted_content_not_high_confidence_simplified"
            )
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return ChineseSubtitleConversionResult(
        source_evidence=source_evidence,
        output_evidence=output_evidence,
        dialogue_line_count=dialogue_count,
        changed_dialogue_line_count=changed_count,
    )


def _convert_subtitle_dialogue(text: str, suffix: str) -> tuple[str, int, int]:
    if suffix in {".ass", ".ssa"}:
        return _convert_ass_dialogue(text)
    return _convert_timed_text_dialogue(text)


def _convert_ass_dialogue(text: str) -> tuple[str, int, int]:
    output: list[str] = []
    dialogue_count = 0
    changed_count = 0
    for line in text.splitlines(keepends=True):
        if not line.lstrip().lower().startswith("dialogue:"):
            output.append(line)
            continue
        head, separator, body = line.partition(":")
        fields = body.split(",", 9)
        if len(fields) != 10:
            output.append(line)
            continue
        dialogue_count += 1
        converted_body = _convert_dialogue_text(fields[9])
        changed_count += converted_body != fields[9]
        output.append(
            head + separator + ",".join(fields[:9]) + "," + converted_body
        )
    return "".join(output), dialogue_count, changed_count


def _convert_timed_text_dialogue(text: str) -> tuple[str, int, int]:
    output: list[str] = []
    in_cue = False
    dialogue_count = 0
    changed_count = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        stripped = content.strip()
        if not stripped:
            in_cue = False
        elif _TIMESTAMP_RE.match(stripped):
            in_cue = True
        elif in_cue:
            dialogue_count += 1
            converted = _convert_dialogue_text(content)
            changed_count += converted != content
            line = converted + ending
        output.append(line)
    return "".join(output), dialogue_count, changed_count


def _convert_dialogue_text(text: str) -> str:
    if _ASS_DRAWING_RE.search(text):
        return text
    output: list[str] = []
    for segment in _ASS_BREAK_RE.split(text):
        if _ASS_BREAK_RE.fullmatch(segment):
            output.append(segment)
            continue
        plain = _PROTECTED_TAG_RE.sub("", segment)
        if _FOREIGN_SCRIPT_RE.search(plain):
            output.append(segment)
            continue
        parts = _PROTECTED_TAG_RE.split(segment)
        output.append(
            "".join(
                part if index % 2 else zhconv(part, "zh-cn")
                for index, part in enumerate(parts)
            )
        )
    return "".join(output)


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
