from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ = sys.path.insert(0, str(PROJECT_ROOT))

from src.rename.utils import VIDEO_SUFFIX

DEFAULT_OBSERVATIONS_DIR = (
    PROJECT_ROOT
    / "tests"
    / "sample_pool"
    / "generated"
    / "main_flow_full_round6"
    / "observations"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tests" / "sample_pool" / "generated" / "main_flow_audit"

SUPPLEMENTAL_RE = re.compile(
    r"(?i)(?:\b(?:ncop|nced|op|ed|pv|cm|menu|trailer|preview|creditless|bonus|extra|extras|special|sp|ova|oad|radio|live|interview)\b|"
    r"特典|映像特典)"
)
TV_TARGET_RE = re.compile(r"/Season\s+(?P<folder>\d{2})/.*\bS(?P<season>\d{2})E(?P<episode>\d{2,3})\b", re.IGNORECASE)
SOURCE_EXPLICIT_EPISODE_RE = re.compile(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})\b", re.IGNORECASE)
WINDOWS_ILLEGAL_RE = re.compile(r'[<>:"|?*]')


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return path.as_posix()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalized_path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\\", "/")).casefold()


def has_video_suffix(path: str) -> bool:
    return Path(path).suffix.casefold() in {suffix.casefold() for suffix in VIDEO_SUFFIX}


def raw_video_sources(sample_json: Path) -> set[str]:
    payload = read_json(sample_json)
    files = payload.get("files") or []
    sources: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or item.get("relative_path") or "").replace("\\", "/")
        if raw_path and has_video_suffix(raw_path):
            sources.add(normalized_path_key(raw_path))
    return sources


def path_violations(label: str, value: str) -> list[str]:
    normalized = value.replace("\\", "/")
    violations: list[str] = []
    if not normalized:
        violations.append(f"{label}:empty_path")
        return violations
    if Path(normalized).is_absolute() or re.match(r"^[A-Za-z]:/", normalized):
        violations.append(f"{label}:absolute_path")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        violations.append(f"{label}:unsafe_segment")
    for part in parts:
        if WINDOWS_ILLEGAL_RE.search(part):
            violations.append(f"{label}:windows_illegal_char")
        if part.rstrip(" .") != part:
            violations.append(f"{label}:trailing_space_or_dot")
    return violations


def route_key(route: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(route.get("route_type") or ""),
        str(route.get("tmdb_id") or ""),
        str(route.get("season_id") if route.get("season_id") is not None else ""),
        str(route.get("target_root_rel") or ""),
    )


def audit_observation(observation_path: Path) -> dict[str, Any]:
    observation = read_json(observation_path)
    sample_id = str(observation.get("sample_id") or observation_path.stem)
    sample_json_path = PROJECT_ROOT / str(observation.get("sample_json") or "")
    payload = observation.get("payload") if isinstance(observation.get("payload"), dict) else {}
    summary = observation.get("summary") if isinstance(observation.get("summary"), dict) else {}
    routes = [item for item in payload.get("routes") or [] if isinstance(item, dict)]
    mapping = [item for item in payload.get("mapping") or [] if isinstance(item, dict)]
    task_artifacts = [item for item in payload.get("task_artifacts") or [] if isinstance(item, dict)]
    record_artifacts = [item for item in payload.get("record_artifacts") or [] if isinstance(item, dict)]
    library_files = [item for item in payload.get("library_files") or [] if isinstance(item, dict)]
    status = str(observation.get("process_status") or "unknown")

    hard: list[str] = []
    warnings: list[str] = []

    if observation.get("uses_runtime_rename_process") is not True:
        hard.append("contract:not_runtime_rename_process")
    if observation.get("uses_shadow_candidate_logic") is not False:
        hard.append("contract:shadow_candidate_logic")
    if bool(observation.get("infra_failure")):
        hard.append("contract:infra_failure")

    raw_sources = raw_video_sources(sample_json_path) if sample_json_path.exists() else set()
    if not sample_json_path.exists():
        hard.append("raw:sample_json_missing")

    mapped_sources = [str(item.get("source_rel") or "").replace("\\", "/") for item in mapping]
    mapped_targets = [str(item.get("target_rel") or "").replace("\\", "/") for item in mapping]
    mapped_source_keys = [normalized_path_key(item) for item in mapped_sources if item]
    mapped_target_keys = [normalized_path_key(item) for item in mapped_targets if item]

    source_counts = Counter(mapped_source_keys)
    target_counts = Counter(mapped_target_keys)
    duplicate_sources = sorted(key for key, count in source_counts.items() if count > 1)
    duplicate_targets = sorted(key for key, count in target_counts.items() if count > 1)
    if duplicate_sources:
        hard.append("mapping:duplicate_source")
    if duplicate_targets:
        hard.append("mapping:duplicate_target")
    for source in mapped_sources:
        hard.extend(path_violations("source_rel", source))
    for target in mapped_targets:
        hard.extend(path_violations("target_rel", target))
    for source_key in mapped_source_keys:
        if raw_sources and source_key not in raw_sources:
            hard.append("mapping:source_not_in_raw_sample")
            break

    video_library_paths = [
        str(item.get("path") or "").replace("\\", "/")
        for item in library_files
        if has_video_suffix(str(item.get("path") or ""))
    ]
    video_library_keys = {normalized_path_key(item) for item in video_library_paths if item}
    for target_key in mapped_target_keys:
        if video_library_keys and target_key not in video_library_keys:
            hard.append("mapping:target_missing_from_library")
            break
    extra_library_outputs = sorted(video_library_keys - set(mapped_target_keys))
    if extra_library_outputs:
        hard.append("library:unmapped_video_output")

    if int(summary.get("mapping_count") or 0) != len(mapping):
        hard.append("summary:mapping_count_mismatch")
    if int(summary.get("target_count") or 0) != len(set(mapped_target_keys)):
        hard.append("summary:target_count_mismatch")
    if status == "executed" and len(mapping) != len(set(mapped_target_keys)):
        hard.append("summary:mapping_target_count_mismatch")

    failure_reasons = sorted(
        {
            str(item.get("failure_reason") or "")
            for item in task_artifacts
            if str(item.get("failure_reason") or "")
        }
    )
    if status == "executed" and failure_reasons:
        hard.append("executed:failure_artifact_present")
    if status == "executed" and observation.get("message"):
        hard.append("executed:non_empty_message")
    if status == "executed" and not mapping:
        hard.append("executed:empty_mapping")
    if status == "executed" and not task_artifacts:
        hard.append("executed:missing_task_artifacts")
    if status == "executed" and not record_artifacts:
        hard.append("executed:missing_record_artifacts")

    mapped_route_types = {str(item.get("route_type") or "") for item in mapping if item.get("route_type")}
    route_types = {str(item.get("route_type") or "") for item in routes if item.get("route_type")}
    final_type = str(summary.get("final_type") or payload.get("final_type") or "unknown")
    if status == "executed" and final_type == "unknown":
        hard.append("route:executed_unknown_final_type")
    if status == "executed" and mapped_route_types - route_types:
        hard.append("route:mapping_route_not_declared")
    if status == "executed" and final_type == "mixed" and not {"tv", "movie"}.issubset(mapped_route_types):
        warnings.append("route:mixed_without_tv_movie_mapping")
    if status == "executed" and final_type in {"tv", "movie"} and any(route_type != final_type for route_type in mapped_route_types):
        hard.append("route:final_type_mapping_mismatch")
    for route in routes:
        if status == "executed" and int(route.get("mapping_count") or 0) == 0:
            hard.append("route:zero_mapping_route")
        if status == "executed" and int(route.get("mapping_count") or 0) > 0:
            if route.get("tmdb_id") is None:
                hard.append("route:mapped_route_missing_tmdb_id")
            if not str(route.get("target_root_rel") or ""):
                hard.append("route:mapped_route_missing_target_root")

    route_mapping_sum = sum(int(route.get("mapping_count") or 0) for route in routes)
    if status == "executed" and route_mapping_sum != len(mapping):
        hard.append("route:mapping_sum_mismatch")

    for item in mapping:
        target_rel = str(item.get("target_rel") or "").replace("\\", "/")
        source_rel = str(item.get("source_rel") or "").replace("\\", "/")
        if item.get("route_type") == "tv":
            match = TV_TARGET_RE.search(target_rel)
            if not match:
                hard.append("tv:target_shape_missing_season_episode")
                continue
            folder_season = int(match.group("folder"))
            filename_season = int(match.group("season"))
            episode = int(match.group("episode"))
            if folder_season != filename_season:
                hard.append("tv:season_folder_filename_mismatch")
            if episode <= 0:
                hard.append("tv:non_positive_episode")
            explicit = SOURCE_EXPLICIT_EPISODE_RE.search(source_rel)
            if explicit:
                if int(explicit.group("season")) != filename_season or int(explicit.group("episode")) != episode:
                    hard.append("tv:explicit_source_episode_mismatch")
            if folder_season == 0 and not SUPPLEMENTAL_RE.search(source_rel):
                warnings.append("tv:season_zero_without_special_cue")
        elif item.get("route_type") == "movie":
            if re.search(r"/Season\s+\d{2}/", target_rel, re.IGNORECASE):
                hard.append("movie:target_contains_season_folder")
            if SOURCE_EXPLICIT_EPISODE_RE.search(target_rel):
                warnings.append("movie:target_contains_episode_token")

    raw_source_values_by_key = {source: source for source in raw_sources}
    unmapped_source_keys = raw_sources - set(mapped_source_keys)
    unmapped_potential_main_keys = {
        source_key
        for source_key in unmapped_source_keys
        if not SUPPLEMENTAL_RE.search(raw_source_values_by_key.get(source_key, source_key))
    }
    unmapped_video_count = len(unmapped_source_keys)
    unmapped_potential_main_count = len(unmapped_potential_main_keys)
    if status == "executed" and unmapped_potential_main_count:
        warnings.append("coverage:unmapped_potential_main_videos")
    if status == "executed" and len(routes) > 1:
        warnings.append("route:multi_route_manual_review")
    if status == "executed":
        season_zero_sources = [
            str(item.get("source_rel") or "")
            for item in mapping
            if item.get("route_type") == "tv" and "/Season 00/" in str(item.get("target_rel") or "")
        ]
        if season_zero_sources and not all(SUPPLEMENTAL_RE.search(source) for source in season_zero_sources):
            warnings.append("route:season_zero_manual_review")

    if status == "executed":
        if hard:
            audited_status = "unsafe_executed"
        elif warnings:
            audited_status = "manual_review"
        else:
            audited_status = "auto_structural_pass"
    elif status == "product_failed":
        has_side_effects = bool(mapping or record_artifacts or video_library_paths)
        audited_status = "partial_side_effect_failure" if has_side_effects else "valid_fail_closed"
        if has_side_effects:
            hard.append("failure:side_effects_present")
    elif bool(observation.get("infra_failure")) or status in {"infra_failed", "runner_error"}:
        audited_status = "retryable_infra_or_dependency"
    else:
        audited_status = "manual_review"

    return {
        "sample_id": sample_id,
        "sample_json": rel(sample_json_path),
        "process_status": status,
        "audited_status": audited_status,
        "hard_violations": sorted(set(hard)),
        "warnings": sorted(set(warnings)),
        "failure_reasons": failure_reasons,
        "metrics": {
            "raw_video_count": len(raw_sources),
            "mapped_video_count": len(mapping),
            "target_count": len(set(mapped_target_keys)),
            "route_count": len(routes),
            "task_artifact_count": len(task_artifacts),
            "record_artifact_count": len(record_artifacts),
            "library_video_count": len(video_library_paths),
            "unmapped_video_count": unmapped_video_count,
            "unmapped_potential_main_count": unmapped_potential_main_count,
        },
    }


def run_audit(observations_dir: Path, output_dir: Path) -> dict[str, Any]:
    observations = sorted(observations_dir.glob("*.json"), key=lambda item: item.name.casefold())
    results = [audit_observation(path) for path in observations]
    status_counts = Counter(str(item["audited_status"]) for item in results)
    process_status_counts = Counter(str(item["process_status"]) for item in results)
    warning_counts = Counter(
        str(warning)
        for item in results
        for warning in item.get("warnings", [])
    )
    hard_violation_counts = Counter(
        str(violation)
        for item in results
        for violation in item.get("hard_violations", [])
    )
    failure_reason_counts = Counter(
        str(reason)
        for item in results
        for reason in item.get("failure_reasons", [])
    )
    summary = {
        "artifact_type": "sample_pool_main_flow_audit_summary",
        "schema_version": 1,
        "observations_dir": rel(observations_dir),
        "sample_count": len(results),
        "process_status_counts": dict(process_status_counts),
        "audited_status_counts": dict(status_counts),
        "warning_counts": dict(warning_counts),
        "hard_violation_counts": dict(hard_violation_counts),
        "failure_reason_counts": dict(failure_reason_counts),
        "unsafe_executed_sample_ids": [item["sample_id"] for item in results if item["audited_status"] == "unsafe_executed"],
        "manual_review_sample_ids": [item["sample_id"] for item in results if item["audited_status"] == "manual_review"],
        "partial_side_effect_failure_sample_ids": [
            item["sample_id"] for item in results if item["audited_status"] == "partial_side_effect_failure"
        ],
    }
    write_json(output_dir / "sample_pool_main_flow_audit_summary.json", summary)
    write_json(output_dir / "sample_pool_main_flow_audit_results.json", {"results": results})
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit main-flow sample-pool observations without calling AI/TMDB.")
    parser.add_argument("--observations-dir", type=Path, default=DEFAULT_OBSERVATIONS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = run_audit(args.observations_dir, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
