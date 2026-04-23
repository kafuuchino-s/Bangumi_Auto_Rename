from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ = sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("BANGUMI_CONFIG_READONLY", "1")


VIDEO_SUFFIXES = {
    ".mkv",
    ".mp4",
    ".avi",
    ".m2ts",
    ".mov",
    ".wmv",
    ".flv",
    ".ts",
}

MIXED_MOVIE_TOKENS = {
    "movie",
    "the movie",
    "gekijouban",
    "avvenire",
    "arietta",
    "crepuscolo",
    "benedizione",
    "providenc",
    "sinners of the system",
    "mugen ressha hen",
}


def load_raw_sample(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid sample root object: {path}")
    return data


def to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [to_json_safe(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return to_json_safe(model_dump())
    return str(value)


def build_runtime_local_files(raw_sample: dict[str, Any]) -> list[dict[str, Any]]:
    local_files: list[dict[str, Any]] = []
    for item in raw_sample.get("files", []):
        if not isinstance(item, dict):
            continue
        path_value = str(item.get("path", ""))
        if Path(path_value).suffix.lower() not in VIDEO_SUFFIXES:
            continue
        local_file: dict[str, Any] = {
            "path": path_value,
            "filename": Path(path_value).name,
            "size": item.get("size", 0),
        }
        if "duration" in item:
            local_file["duration"] = item["duration"]
        local_files.append(local_file)
    return local_files


def hydrate_tv_file_paths(
    candidate: dict[str, Any], local_files: list[dict[str, Any]]
) -> dict[str, Any]:
    file_mapping = candidate.get("file_mapping")
    if not isinstance(file_mapping, list) or not file_mapping:
        return candidate

    hydrated_mapping: list[dict[str, Any]] = []
    hydration_notes: list[str] = []
    conflict_details = candidate.get("conflict_details")
    merged_conflicts = list(conflict_details) if isinstance(conflict_details, list) else []

    for item in file_mapping:
        if not isinstance(item, dict):
            continue

        normalized = dict(item)
        file_path = normalized.get("file_path")
        source_index = normalized.get("source_index")

        if file_path:
            hydrated_mapping.append(normalized)
            continue

        if not isinstance(source_index, int) or source_index < 1:
            merged_conflicts.append(f"invalid_source_index:{source_index}")
            hydrated_mapping.append(normalized)
            continue

        zero_based_index = source_index - 1
        if zero_based_index < 0 or zero_based_index >= len(local_files):
            merged_conflicts.append(f"missing_source_index:{source_index}")
            hydrated_mapping.append(normalized)
            continue

        source_file = local_files[zero_based_index]
        source_path = source_file.get("path")
        if isinstance(source_path, str) and source_path.strip():
            normalized["file_path"] = source_path
            hydration_notes.append(f"hydrated:{source_index}:{source_path}")
        else:
            merged_conflicts.append(f"empty_source_path:{source_index}")

        hydrated_mapping.append(normalized)

    candidate["file_mapping"] = hydrated_mapping
    if hydration_notes:
        candidate["hydration_notes"] = {
            "hydrated_count": len(hydration_notes),
            "examples": hydration_notes[:5],
        }
    if merged_conflicts:
        candidate["conflict_details"] = merged_conflicts
    return candidate


def has_mixed_bundle_cues(local_files: list[dict[str, Any]]) -> bool:
    video_paths = [str(item.get("path", "")) for item in local_files]
    joined = "\n".join(video_paths).casefold()
    if any(token in joined for token in MIXED_MOVIE_TOKENS):
        return True
    bundle_tokens = ["extras/", "extra/", "sp/", "sps/", "menu", "ncop", "nced", "bonus/"]
    return sum(1 for token in bundle_tokens if token in joined) >= 2


def extract_movie_like_paths_from_local_files(local_files: list[dict[str, Any]]) -> list[str]:
    results: list[str] = []
    for item in local_files:
        path_value = str(item.get("path") or "")
        lowered = path_value.casefold()
        if not any(token in lowered for token in MIXED_MOVIE_TOKENS):
            continue
        if any(token in lowered for token in ("menu", "cm", "pv", "ncop", "nced", "bonus/", "extra/", "extras/")):
            continue
        results.append(path_value)
    return results


def should_try_dual_route(
    local_files: list[dict[str, Any]], task_plan: dict[str, Any] | None
) -> bool:
    if len(local_files) <= 1:
        return False
    if task_plan is not None:
        tv_available = bool((task_plan.get("tv_candidate") or {}).get("available"))
        movie_available = bool((task_plan.get("movie_candidate") or {}).get("available"))
        if tv_available and movie_available:
            return True
    return has_mixed_bundle_cues(local_files)


def extract_movie_like_paths(movie_candidate_result: dict[str, Any]) -> list[str]:
    result: list[str] = []
    collection_analysis = movie_candidate_result.get("collection_analysis")
    if isinstance(collection_analysis, dict):
        mapping = collection_analysis.get("file_mapping")
        if isinstance(mapping, list):
            for item in mapping:
                if not isinstance(item, dict):
                    continue
                path_value = str(item.get("file_path") or "")
                movie_title = str(item.get("movie_title") or "")
                haystack = f"{path_value}\n{movie_title}".casefold()
                if any(token in haystack for token in MIXED_MOVIE_TOKENS):
                    result.append(path_value)
    return result


def compact_route_result(route_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(route_result, dict):
        return None

    compact: dict[str, Any] = {
        "status": route_result.get("status"),
        "type": route_result.get("type"),
        "title_candidate": route_result.get("title_candidate"),
        "timing": route_result.get("timing"),
    }

    file_mapping = route_result.get("file_mapping")
    if isinstance(file_mapping, list):
        compact["file_mapping_count"] = len(file_mapping)
        compact["has_regular"] = any(
            isinstance(item, dict) and item.get("episode_type") == "regular"
            for item in file_mapping
        )
        compact["has_special"] = any(
            isinstance(item, dict) and item.get("episode_type") == "special"
            for item in file_mapping
        )
        compact["has_movie_like"] = any(
            isinstance(item, dict) and item.get("episode_type") == "movie"
            for item in file_mapping
        )

    analysis_result = route_result.get("analysis_result")
    if isinstance(analysis_result, dict):
        compact["analysis_result"] = analysis_result

    collection_analysis = route_result.get("collection_analysis")
    if isinstance(collection_analysis, dict):
        compact["collection_analysis"] = {
            "is_collection": collection_analysis.get("is_collection"),
            "confidence": collection_analysis.get("confidence"),
            "reason": collection_analysis.get("reason"),
            "file_mapping_count": len(collection_analysis.get("file_mapping") or []),
            "unmatched_count": len(collection_analysis.get("unmatched_files") or []),
            "conflict_count": len(collection_analysis.get("conflict_details") or []),
            "movie_like_paths": extract_movie_like_paths(route_result),
        }

    primary_files = route_result.get("primary_files")
    if isinstance(primary_files, list):
        compact["primary_files"] = primary_files[:5]

    return to_json_safe(compact)


def infer_final_type(candidate: dict[str, Any]) -> str:
    tv_candidate_result = candidate.get("tv_candidate_result")
    movie_candidate_result = candidate.get("movie_candidate_result")
    if isinstance(tv_candidate_result, dict) and isinstance(movie_candidate_result, dict):
        tv_has_regular = bool(tv_candidate_result.get("has_regular"))
        collection_analysis = movie_candidate_result.get("collection_analysis")
        movie_like_paths: list[str] = []
        if isinstance(collection_analysis, dict):
            movie_like_paths.extend(list(collection_analysis.get("movie_like_paths") or []))
        movie_like_paths.extend(list(candidate.get("movie_like_paths_from_paths") or []))
        movie_like_paths = list(dict.fromkeys(movie_like_paths))
        if tv_has_regular and movie_like_paths:
            return "mixed"

    candidate_type = str(candidate.get("type") or "")

    if candidate_type == "movie":
        collection_analysis = candidate.get("collection_analysis")
        if isinstance(collection_analysis, dict):
            is_collection = bool(collection_analysis.get("is_collection"))
            reason = str(collection_analysis.get("reason") or "")
            file_mapping = collection_analysis.get("file_mapping")
            unmatched_files = collection_analysis.get("unmatched_files")
            mapping_count = len(file_mapping) if isinstance(file_mapping, list) else 0
            unmatched_count = len(unmatched_files) if isinstance(unmatched_files, list) else 0
            tv_reason_tokens = ["剧集", "动画", "特典", "非电影合集", "不是电影合集", "TV"]
            if (
                not is_collection
                and mapping_count == 0
                and unmatched_count >= 3
                and any(token in reason for token in tv_reason_tokens)
            ):
                return "tv"
        return "movie"

    file_mapping = candidate.get("file_mapping")
    if not isinstance(file_mapping, list) or not file_mapping:
        return candidate_type or "unknown"

    has_regular = False
    has_movie_like = False
    has_special = False
    for item in file_mapping:
        if not isinstance(item, dict):
            continue
        episode_type = str(item.get("episode_type") or "")
        if episode_type == "regular":
            has_regular = True
        elif episode_type == "movie":
            has_movie_like = True
        elif episode_type == "special":
            has_special = True

    if has_regular and has_movie_like:
        return "mixed"
    if has_regular or has_special:
        return "tv"
    if has_movie_like:
        return "movie"
    return candidate_type or "unknown"


def resolve_title_and_type(sample_name: str) -> tuple[str | None, str | None]:
    from src.ai.client import AIClient
    from src.rename.process import Rename

    ai_client = AIClient()
    extracted = ai_client.extract_title_and_type(sample_name)
    if extracted:
        return extracted[0], extracted[1]

    root_path = Path(sample_name)
    clean_name, _, _, _, inferred_type = Rename._build_title_inputs(root_path)
    media_type = None
    if inferred_type == "tv":
        media_type = "tv"
    elif inferred_type == "movie":
        media_type = "movie"
    return clean_name or sample_name, media_type


def build_task_plan(sample_name: str, local_files: list[dict[str, Any]]) -> dict[str, Any] | None:
    from src.rename.process import Rename, TaskTypePlan

    if not local_files:
        return None

    rename = Rename()
    fake_path = Path(sample_name)
    plan = rename.check_task_type(
        sample_name,
        0,
        fake_path,
        None,
        None,
        None,
        False,
        None,
        None,
        None,
    )
    if isinstance(plan, str):
        return None
    return dict(plan)


def infer_media_type_from_files(local_files: list[dict[str, Any]]) -> str | None:
    if not local_files:
        return None

    if len(local_files) == 1:
        return "movie"

    joined_paths = "\n".join(str(item.get("path", "")) for item in local_files)
    upper_paths = joined_paths.upper()
    special_tokens = ["SP/", "SPS/", "PV", "NCOP", "NCED", "MENU", "SPECIAL"]
    special_hits = sum(1 for token in special_tokens if token in upper_paths)
    if special_hits >= 2:
        return "movie"

    return None


def build_tv_candidate(
    sample_name: str,
    local_files: list[dict[str, Any]],
    task_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from src.ai.client import AIClient
    from src.rename.get_info import Search

    ai_client = AIClient()
    search = Search()
    title, _ = resolve_title_and_type(sample_name)
    title = title or sample_name

    plan_tv_candidate = (task_plan or {}).get("tv_candidate") if task_plan else None
    if isinstance(plan_tv_candidate, dict) and plan_tv_candidate.get("available"):
        name = str(plan_tv_candidate.get("name") or title)
        tv_info = plan_tv_candidate.get("info")
    else:
        name, tv_info = search.get_tv_info(title, 0)
    if not name or not tv_info:
        return {
            "status": "tmdb_not_found",
            "type": "tv",
            "title_candidate": title,
            "file_mapping": [],
        }
    hydrated = search.fill_season_info(tv_info)
    start_time = time.time()
    ai_result = ai_client.analyze_episode_mapping(hydrated, local_files)
    elapsed_seconds = round(time.time() - start_time, 3)

    if ai_result is not None and not ai_result.file_mapping:
        seasons = hydrated.get("seasons")
        if isinstance(seasons, list):
            season_zero = next(
                (season for season in seasons if isinstance(season, dict) and season.get("season_number") == 0),
                None,
            )
            season_zero_episodes = []
            if isinstance(season_zero, dict):
                raw_episodes = season_zero.get("episodes")
                if isinstance(raw_episodes, list):
                    season_zero_episodes = [ep for ep in raw_episodes if isinstance(ep, dict)]

            if 0 < len(local_files) <= 4 and len(season_zero_episodes) >= len(local_files):
                fallback_mappings = []
                for index, file_info in enumerate(local_files, 1):
                    episode = season_zero_episodes[index - 1]
                    fallback_mappings.append(
                        {
                            "source_index": index,
                            "file_path": file_info["path"],
                            "tmdb_season": 0,
                            "tmdb_episode": int(episode.get("episode_number") or index),
                            "episode_type": "special",
                            "confidence": "Medium",
                        }
                    )

                candidate = {
                    "status": "ok",
                    "type": "tv",
                    "title_candidate": title,
                    "tmdb_name": name,
                    "tmdb_id": hydrated.get("id"),
                    "timing": {"analysis_seconds": elapsed_seconds},
                    "file_mapping": fallback_mappings,
                    "conflict_details": [],
                    "analysis_result": {
                        "confidence": "Medium",
                        "reason": "按 TMDB Season 0 顺序回退映射 OVA/特别篇。",
                        "season_mapping": [
                            {
                                "local_group_name": sample_name,
                                "maps_to_tmdb_seasons": [0],
                            }
                        ],
                        "file_mapping_count": len(fallback_mappings),
                        "unmatched_count": 0,
                        "conflict_count": 0,
                    },
                }
                return candidate

    analysis_summary = None
    if ai_result is not None:
        analysis_summary = {
            "confidence": ai_result.confidence,
            "reason": ai_result.reason,
            "season_mapping": [item.model_dump() for item in ai_result.season_mapping],
            "file_mapping_count": len(ai_result.file_mapping),
            "unmatched_count": len(ai_result.unmatched_files),
            "conflict_count": len(ai_result.conflict_details),
        }
    candidate = {
        "status": "ok" if ai_result else "ai_failed",
        "type": "tv",
        "title_candidate": title,
        "tmdb_name": name,
        "tmdb_id": hydrated.get("id"),
        "timing": {"analysis_seconds": elapsed_seconds},
        "file_mapping": ai_result.model_dump().get("file_mapping", []) if ai_result else [],
        "conflict_details": ai_result.model_dump().get("conflict_details", []) if ai_result else [],
        "analysis_result": analysis_summary,
    }
    return hydrate_tv_file_paths(candidate, local_files)


def build_movie_candidate(
    sample_name: str,
    local_files: list[dict[str, Any]],
    task_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from src.ai.client import AIClient
    from src.rename.get_info import Search

    ai_client = AIClient()
    search = Search()
    title, _ = resolve_title_and_type(sample_name)
    title = title or sample_name

    plan_movie_candidate = (task_plan or {}).get("movie_candidate") if task_plan else None

    if len(local_files) > 1:
        start_time = time.time()
        collection_result = ai_client.analyze_movie_collection(title, local_files)
        elapsed_seconds = round(time.time() - start_time, 3)
        return {
            "status": "ok" if collection_result else "ai_failed",
            "type": "movie",
            "title_candidate": title,
            "timing": {"analysis_seconds": elapsed_seconds},
            "collection_analysis": collection_result.model_dump() if collection_result else None,
        }

    start_time = time.time()
    if isinstance(plan_movie_candidate, dict) and plan_movie_candidate.get("available"):
        name = str(plan_movie_candidate.get("name") or title)
        movie_info = plan_movie_candidate.get("info")
    else:
        name, movie_info = search.get_movie_info(title, 0)
    elapsed_seconds = round(time.time() - start_time, 3)
    return {
        "status": "ok" if movie_info else "tmdb_not_found",
        "type": "movie",
        "title_candidate": title,
        "tmdb_name": name,
        "tmdb_id": movie_info.get("id") if movie_info else None,
        "timing": {"analysis_seconds": elapsed_seconds},
        "primary_files": [{"file_path": item["path"]} for item in local_files],
    }


def build_candidate(raw_sample: dict[str, Any]) -> dict[str, Any]:
    sample_name = str(raw_sample.get("root_name", "unknown"))
    local_files = build_runtime_local_files(raw_sample)
    task_plan = build_task_plan(sample_name, local_files)
    tv_candidate_result = None
    movie_candidate_result = None
    movie_like_paths_from_paths = extract_movie_like_paths_from_local_files(local_files)

    if should_try_dual_route(local_files, task_plan):
        tv_candidate_result = build_tv_candidate(sample_name, local_files, task_plan)
        movie_candidate_result = build_movie_candidate(sample_name, local_files, task_plan)

    if task_plan is not None:
        media_type = "movie" if task_plan.get("is_movie") else "tv"
    else:
        _, media_type = resolve_title_and_type(sample_name)
        inferred_media_type = infer_media_type_from_files(local_files)
        if inferred_media_type == "movie":
            media_type = "movie"

    if media_type == "movie":
        candidate = movie_candidate_result or build_movie_candidate(sample_name, local_files, task_plan)
    else:
        candidate = tv_candidate_result or build_tv_candidate(sample_name, local_files, task_plan)

    if task_plan is not None:
        candidate["task_plan"] = {
            "is_movie": task_plan.get("is_movie"),
            "ai_type": task_plan.get("ai_type"),
            "tv_available": bool((task_plan.get("tv_candidate") or {}).get("available")),
            "movie_available": bool((task_plan.get("movie_candidate") or {}).get("available")),
            "selected_confidence": task_plan.get("selected_confidence"),
        }
    if tv_candidate_result is not None:
        candidate["tv_candidate_result"] = compact_route_result(tv_candidate_result)
    if movie_candidate_result is not None:
        candidate["movie_candidate_result"] = compact_route_result(movie_candidate_result)
    if movie_like_paths_from_paths:
        candidate["movie_like_paths_from_paths"] = movie_like_paths_from_paths[:8]
    candidate["final_type"] = infer_final_type(candidate)
    return candidate


def generate_candidate_file(sample_file: Path, output_dir: Path) -> Path:
    raw_sample = load_raw_sample(sample_file)
    candidate = to_json_safe(build_candidate(raw_sample))
    output_file = output_dir / f"{sample_file.stem}.candidate.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(candidate, f, ensure_ascii=False, indent=2)
    print(f"[generated] {output_file}")
    return output_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate candidate outputs from raw sample pool JSON files")
    _ = parser.add_argument("input", help="Raw sample file or directory")
    _ = parser.add_argument("output", help="Generated candidate output directory")
    _ = parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of worker processes for directory mode (default: 10)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    concurrency = max(1, int(args.concurrency))

    sample_files: list[Path]
    if input_path.is_dir():
        sample_files = sorted(input_path.rglob("*.json"))
    else:
        sample_files = [input_path]

    if len(sample_files) <= 1 or concurrency == 1:
        for sample_file in sample_files:
            _ = generate_candidate_file(sample_file, output_dir)
        return

    with concurrent.futures.ProcessPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(generate_candidate_file, sample_file, output_dir)
            for sample_file in sample_files
        ]
        for future in concurrent.futures.as_completed(futures):
            _ = future.result()


if __name__ == "__main__":
    main()
