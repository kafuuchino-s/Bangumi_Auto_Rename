#!/usr/bin/env python3
"""字幕 Case Agent Pi 真起 smoke（mapping-only，不落盘）。

最小 2 字幕 + 2 目标视频，走 ``run_subtitle_case_agent_pi`` 与生产相同
``_prepare_pi_runtime_model_config``（含 anthropic_messages 等全局配置）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.subtitle.case_agent.evidence_broker import build_target_video_cards
from src.subtitle.case_agent.local_subtitle_entry import build_subtitle_file_cards
from src.subtitle.case_agent.pi_runner import run_subtitle_case_agent_pi
from src.subtitle.case_agent.workspace import build_subtitle_case_workspace


def _lang(lang: str) -> tuple[str, bool]:
    table = {"chs": ("zh-CN", True), "cht": ("zh-TW", False)}
    return table.get((lang or "").lower().strip(), ("zh-CN", True))


def main() -> int:
    tmp = REPO_ROOT / "data" / "subtitle_case_agent" / "smoke_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    target_dir = tmp / "lib" / "Gatchaman Crowds (2013)" / "Season 01"
    target_dir.mkdir(parents=True, exist_ok=True)
    v1, v2 = "Gatchaman Crowds - S01E01 - A.mkv", "Gatchaman Crowds - S01E02 - B.mkv"
    (target_dir / v1).write_bytes(b"x")
    (target_dir / v2).write_bytes(b"x")
    tasks = [
        {
            "uuid": "smoke-t1",
            "title": "Gatchaman Crowds",
            "season": 1,
            "is_movie": False,
            "videos": [v1, v2],
            "target_dir": str(target_dir),
            "video_targets": {v1: str(target_dir / v1), v2: str(target_dir / v2)},
            "source_videos": {v1: "01.mkv", v2: "02.mkv"},
        }
    ]
    subs = [
        SimpleNamespace(archive_path="S1/01.chs.ass", filename="01.chs.ass"),
        SimpleNamespace(archive_path="S1/02.chs.ass", filename="02.chs.ass"),
    ]
    cards = build_subtitle_file_cards(subs)
    ws = build_subtitle_case_workspace(
        archive_name="smoke-sub.zip",
        subtitle_files=cards,
        target_videos=build_target_video_cards(tasks),
    )
    started = time.time()
    result = run_subtitle_case_agent_pi(
        workspace=ws,
        language_resolver=_lang,
        source_path=tmp / "smoke-sub.zip",
        archive_name="smoke-sub.zip",
    )
    elapsed_ms = int((time.time() - started) * 1000)
    out = {
        "ok": result.ok,
        "status": result.status,
        "summary": result.summary,
        "elapsed_ms": elapsed_ms,
        "pi_model": result.pi_model,
        "pi_base_url": result.pi_base_url,
        "run_dir": result.run_dir.as_posix(),
        "mapping_count": len(result.compiled_plan.mappings) if result.compiled_plan else 0,
        "tool_call_counts": dict(result.tool_call_counts),
    }
    if result.run_dir.exists():
        models = result.run_dir / "pi_agent_config" / "models.json"
        if models.exists():
            out["pi_api"] = json.loads(models.read_text(encoding="utf-8"))["providers"]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if result.ok and result.status == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())