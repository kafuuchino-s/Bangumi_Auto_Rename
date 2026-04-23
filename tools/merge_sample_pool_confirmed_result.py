from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping


def load_json_object(path: Path) -> dict[str, object]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return dict(data)


def write_json_object(path: Path, payload: Mapping[str, object]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(payload), f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge a confirmed candidate result back into a raw sample JSON"
    )
    parser.add_argument("sample", help="Path to raw sample JSON")
    parser.add_argument("candidate", help="Path to confirmed candidate JSON")
    parser.add_argument(
        "--field",
        default="confirmed_result",
        help="Target field name inside the sample JSON (default: confirmed_result)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print merged JSON to stdout without writing the sample file",
    )
    args = parser.parse_args()

    sample_path = Path(args.sample)
    candidate_path = Path(args.candidate)
    field_name = str(args.field)
    print_to_stdout = bool(args.stdout)

    if not sample_path.exists():
        raise SystemExit(f"Sample JSON does not exist: {sample_path}")
    if not candidate_path.exists():
        raise SystemExit(f"Candidate JSON does not exist: {candidate_path}")

    sample_data = load_json_object(sample_path)
    candidate_data = load_json_object(candidate_path)

    sample_data[field_name] = candidate_data

    if print_to_stdout:
        print(json.dumps(sample_data, ensure_ascii=False, indent=2))
        return

    write_json_object(sample_path, sample_data)
    print(f"[merged] {candidate_path} -> {sample_path} ({field_name})")


if __name__ == "__main__":
    main()
