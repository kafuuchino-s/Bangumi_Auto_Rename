#!/usr/bin/env python3
"""为 README 截图生成 mock 任务/字幕数据，并保留原始数据备份。

用法：
    .venv\\Scripts\\python.exe tools\\prepare_screenshot_data.py backup
    .venv\\Scripts\\python.exe tools\\prepare_screenshot_data.py generate
    .venv\\Scripts\\python.exe tools\\prepare_screenshot_data.py restore
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils.path import RECORD_PATH, TASK_PATH
from src.utils.utils import write_task

BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / ".screenshot_backup"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"

MOCK_PATH_VALUES = {
    "tv_path": "H:\\Media\\TV",
    "movie_path": "H:\\Media\\Movies",
    "anime_path": "H:\\Media\\Anime Series",
    "anime_movie_path": "H:\\Media\\Anime Movies",
}

MOCK_TASKS = [
    {
        "uuid": "mock-success-tv",
        "name": "Re:从零开始的异世界生活",
        "tmdb_name": "Re:ZERO -Starting Life in Another World-",
        "path": "H:\\Download\\[VCB-Studio] Re Zero kara Hajimeru Isekai Seikatsu [Ma10p_1080p]",
        "season_id": 1,
        "is_anime": True,
        "is_movie": False,
        "error": "",
        "ai_used": True,
        "ai_attempted": True,
        "ai_confidence": "0.97",
        "pipeline_mode": "local_bangumi_to_tmdb_product",
        "failure_reason": "",
        "case_agent_status": "accepted",
    },
    {
        "uuid": "mock-success-movie",
        "name": "鬼灭之刃 无限列车篇",
        "tmdb_name": "Demon Slayer: Kimetsu no Yaiba – The Movie: Mugen Train",
        "path": "H:\\Download\\[BeanSub&FZSD&VCB-Studio] Gekijouban Kimetsu no Yaiba Mugen Ressha Hen [Ma10p_1080p]",
        "season_id": 0,
        "is_anime": True,
        "is_movie": True,
        "error": "",
        "ai_used": True,
        "ai_attempted": True,
        "ai_confidence": "0.95",
        "pipeline_mode": "local_bangumi_to_tmdb_product",
        "failure_reason": "",
        "case_agent_status": "accepted",
    },
    {
        "uuid": "mock-ai-fail",
        "name": "某冷门实验动画",
        "tmdb_name": "",
        "path": "H:\\Download\\Unknown Indie Anime [01]",
        "season_id": None,
        "is_anime": True,
        "is_movie": False,
        "error": "ai_unavailable",
        "ai_used": False,
        "ai_attempted": True,
        "ai_confidence": None,
        "pipeline_mode": "local_bangumi_case_agent_primary",
        "failure_reason": "ai_unavailable",
        "case_agent_status": "",
    },
    {
        "uuid": "mock-need-confirm",
        "name": "复杂合集待确认",
        "tmdb_name": "",
        "path": "H:\\Download\\Mixed Collection [BDMV]",
        "season_id": None,
        "is_anime": True,
        "is_movie": False,
        "error": "need_confirm",
        "ai_used": True,
        "ai_attempted": True,
        "ai_confidence": "0.62",
        "pipeline_mode": "local_bangumi_case_agent_primary",
        "failure_reason": "local_bangumi_case_agent_primary",
        "case_agent_status": "need_confirm",
    },
    {
        "uuid": "mock-bridge-fail",
        "name": "旧剧场版无 TMDB 条目",
        "tmdb_name": "",
        "path": "H:\\Download\\Old OVA [DVDrip]",
        "season_id": None,
        "is_anime": True,
        "is_movie": False,
        "error": "bgm_to_tmdb_bridge_failed",
        "ai_used": True,
        "ai_attempted": True,
        "ai_confidence": "0.88",
        "pipeline_mode": "local_bangumi_to_tmdb_product",
        "failure_reason": "bgm_to_tmdb_bridge_failed",
        "case_agent_status": "accepted",
    },
]

MOCK_SUBTITLE_TASKS = [
    {
        "uuid": "mock-subtitle-ok",
        "type": "subtitle",
        "archive_path": "H:\\Download\\subtitles\\ReZero_S1_Subtitles.zip",
        "matched_task": "mock-success-tv",
        "matched_count": 13,
        "total_subtitles": 13,
        "status": "success",
        "sync_summary": {"enabled": True, "success": 12, "attempted": 13, "fallback": 1},
    },
    {
        "uuid": "mock-subtitle-partial",
        "type": "subtitle",
        "archive_path": "H:\\Download\\subtitles\\Random_Subs.rar",
        "matched_task": "-",
        "matched_count": 2,
        "total_subtitles": 8,
        "status": "failed",
        "sync_summary": {"enabled": False, "success": 0, "attempted": 0, "fallback": 0},
    },
]


def backup() -> None:
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)
    BACKUP_DIR.mkdir(parents=True)
    for src in (TASK_PATH, RECORD_PATH):
        if src.exists():
            dst = BACKUP_DIR / src.name
            shutil.copytree(src, dst)
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, BACKUP_DIR / "config.json")
    print(f"已备份到 {BACKUP_DIR}")


def restore() -> None:
    if not BACKUP_DIR.exists():
        print("备份不存在，无法恢复")
        sys.exit(1)
    for name in ("task", "record"):
        src = BACKUP_DIR / name
        dst = Path("data") / name
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst)
    config_backup = BACKUP_DIR / "config.json"
    if config_backup.exists():
        shutil.copy2(config_backup, CONFIG_PATH)
    print("已恢复原始数据")


def generate() -> None:
    # 清空现有 task/record
    for p in (TASK_PATH, RECORD_PATH):
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)

    # 临时把 config 中真实路径/密钥替换为 mock，避免截图泄露
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    config_snapshot = dict(config)
    for key in ("api_key", "ai_api_key"):
        if key in config and isinstance(config[key], str) and config[key]:
            config[key] = "*" * len(config[key])
    for key, value in MOCK_PATH_VALUES.items():
        config[key] = value
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    base_ts = time.time()
    for idx, task in enumerate(MOCK_TASKS):
        write_task(task["uuid"], task)
        # 设置不同 mtime 保证排序
        p = TASK_PATH / f"{task['uuid']}.json"
        p.touch()
        p_mtime = base_ts - idx * 60
        os.utime(p, (p_mtime, p_mtime))
        # 生成对应 record，让详情页有内容
        record = {
            "target_dir": f"H:\\Media\\{task['name']} (2024)" if task["error"] == "" else "",
            "mappings": [
                {"source": "[VCB-Studio] Re Zero - 01.mkv", "target": "Re Zero - S01E01.mkv"},
                {"source": "[VCB-Studio] Re Zero - 02.mkv", "target": "Re Zero - S01E02.mkv"},
            ] if task["error"] == "" else [],
            "case_agent_status": task.get("case_agent_status"),
            "product_result_kind": "success" if task["error"] == "" else "failed",
        }
        (RECORD_PATH / f"{task['uuid']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    for idx, sub in enumerate(MOCK_SUBTITLE_TASKS):
        write_task(sub["uuid"], sub)
        p = TASK_PATH / f"{sub['uuid']}.json"
        p.touch()
        p_mtime = base_ts - (len(MOCK_TASKS) + idx) * 60
        os.utime(p, (p_mtime, p_mtime))

    print("已生成 mock 数据")
    return config_snapshot


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "backup":
        backup()
    elif cmd == "generate":
        generate()
    elif cmd == "restore":
        restore()
    else:
        print("用法: backup | generate | restore")
        sys.exit(1)
