from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from summarize_local_bangumi_human_trace import summarize_trace


def _sample_result_paths(output_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(output_dir.glob("sample_*.json")):
        name = path.name
        if name.endswith(".progress.json") or ".trace_summary" in name:
            continue
        paths.append(path)
    return paths


def _default_output_dir(sample: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sample_id = str(sample).strip().replace("\\", "/").rsplit("/", 1)[-1]
    sample_id = sample_id.replace(".json", "").replace("sample_", "")
    return Path("tests/sample_pool/generated") / f"local_bangumi_mapping_sample_{sample_id}_human_gate_{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one Local->Bangumi HumanCaseAgent focused gate and write a mechanical trace summary."
    )
    parser.add_argument("--sample", required=True, help="Sample id or raw sample path accepted by run_local_bangumi_mapping_sample_pool.py.")
    parser.add_argument("--output-dir", help="Generated run directory. Defaults to a timestamped directory.")
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--sample-timeout-seconds", type=int, default=300)
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(args.sample)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "tools/run_local_bangumi_mapping_sample_pool.py",
        "--sample",
        str(args.sample),
        "--limit",
        str(args.limit),
        "--max-rounds",
        str(args.max_rounds),
        "--sample-timeout-seconds",
        str(args.sample_timeout_seconds),
        "--output-dir",
        output_dir.as_posix(),
    ]
    completed = subprocess.run(command, text=True)

    trace_summaries: list[dict[str, object]] = []
    result_paths = _sample_result_paths(output_dir)
    for path in result_paths:
        summary = summarize_trace(path)
        trace_summaries.append(summary)
        trace_path = path.with_suffix(".trace_summary.json")
        trace_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    gate_summary = {
        "output_dir": output_dir.as_posix(),
        "runner_exit_code": completed.returncode,
        "trace_summary_count": len(trace_summaries),
        "trace_summary_files": [path.with_suffix(".trace_summary.json").as_posix() for path in result_paths],
    }
    (output_dir / "human_gate_summary.json").write_text(
        json.dumps(gate_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(gate_summary, ensure_ascii=False, indent=2))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
