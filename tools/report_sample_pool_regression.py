from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ = sys.path.insert(0, str(PROJECT_ROOT))

from tools.generate_sample_pool_candidates import infer_final_type


def load_candidate(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize sample pool candidate regression outputs")
    parser.add_argument("input", help="Candidate output directory")
    args = parser.parse_args()

    input_dir = Path(args.input)
    files = sorted(input_dir.glob("*.candidate.json"))
    status_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    final_type_counter: Counter[str] = Counter()
    low_confidence: list[str] = []
    mixed_cases: list[str] = []
    unresolved_cases: list[str] = []

    for file in files:
        data = load_candidate(file)
        status = str(data.get("status") or "unknown")
        status_counter[status] += 1

        top_type = str(data.get("type") or "unknown")
        type_counter[top_type] += 1

        final_type = infer_final_type(data)
        final_type_counter[final_type] += 1
        if final_type == "mixed":
            mixed_cases.append(file.stem)

        confidence = None
        analysis_result = data.get("analysis_result")
        collection_result = data.get("collection_analysis")
        if isinstance(analysis_result, dict):
            confidence = analysis_result.get("confidence")
        elif isinstance(collection_result, dict):
            confidence = collection_result.get("confidence")
        if confidence == "Low":
            low_confidence.append(file.stem)

        if status in {"tmdb_not_found", "ai_failed"}:
            unresolved_cases.append(file.stem)

    print(f"total_candidates={len(files)}")
    print("status_counts=", dict(status_counter))
    print("type_counts=", dict(type_counter))
    print("final_type_counts=", dict(final_type_counter))
    print("low_confidence_count=", len(low_confidence))
    print("mixed_count=", len(mixed_cases))
    print("unresolved_count=", len(unresolved_cases))
    print("low_confidence_examples=", low_confidence[:10])
    print("mixed_examples=", mixed_cases[:10])
    print("unresolved_examples=", unresolved_cases[:10])


if __name__ == "__main__":
    main()
