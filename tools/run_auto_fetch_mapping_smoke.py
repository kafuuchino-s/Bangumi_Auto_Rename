#!/usr/bin/env python3
"""auto_fetch 字幕抓取映射模式 smoke（L2，不落盘）。

用 sample 池两段映射结果（Local→BGM + BGM→TMDB）构造虚拟 task+record，
调 ``SubtitleAutoFetcher.process_task_mapping`` 跑端到端：
搜帖→选帖→选包→下载真包到临时目录→processor.process_mapping 字幕→视频配对，
**不落盘到媒体库，不需要真实落地视频文件**。

对齐 rename 链路 mapping-only 回归：只验映射决策，不造文件、不碰媒体库。

用法::

    # 默认跑 sample_0012（Omoide no Mani 2014，单 movie）
    .venv/Scripts/python.exe tools/run_auto_fetch_mapping_smoke.py

    # 选其他样本（substring 匹配 stage-2 产物文件名）
    .venv/Scripts/python.exe tools/run_auto_fetch_mapping_smoke.py --sample 0006

    # 保留虚拟 task/record 供复盘（默认跑完清理）
    .venv/Scripts/python.exe tools/run_auto_fetch_mapping_smoke.py --keep-task

    # 指定 stage-2 产物目录（默认自动找最新 bgm_to_tmdb_bridge_gate_*）
    .venv/Scripts/python.exe tools/run_auto_fetch_mapping_smoke.py \
        --bridge-root tests/sample_pool/generated/bgm_to_tmdb_bridge_gate_<ts>

产物：tests/sample_pool/generated/auto_fetch_mapping_smoke_<ts>/<sample>.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config.config_manager import cm  # noqa: E402
from src.subtitle.auto_fetch import SubtitleAutoFetcher  # noqa: E402
from src.utils.path import RECORD_PATH, TASK_PATH  # noqa: E402
from src.utils.utils import get_record, get_task, write_task  # noqa: E402

GENERATED_ROOT = REPO_ROOT / "tests" / "sample_pool" / "generated"
DEFAULT_SAMPLE_SUBSTR = "0012"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="auto_fetch mapping smoke (L2, no landing)")
    p.add_argument("--sample", default=DEFAULT_SAMPLE_SUBSTR,
                   help=f"sample substring (default {DEFAULT_SAMPLE_SUBSTR})")
    p.add_argument("--samples", default="",
                   help="comma-separated sample substrings for batch run (e.g. 0005,0012,0040). "
                        "When set, --sample is ignored and each substring runs once.")
    p.add_argument("--bridge-root", default="",
                   help="stage-2 bridge artifact root (default: latest bgm_to_tmdb_bridge_gate_*)")
    p.add_argument("--stage1-root", default="",
                   help="stage-1 local_bangumi artifact root (default: latest local_bangumi_mapping_gate_*)")
    p.add_argument("--keep-task", action="store_true",
                   help="keep synthetic task/record for review (default: clean up)")
    p.add_argument("--output-dir", default="",
                   help="output dir (default: tests/sample_pool/generated/auto_fetch_mapping_smoke_<ts>)")
    p.add_argument("--workers", type=int, default=10,
                   help="number of samples to run concurrently (default 10; use 1 for sequential). "
                        "Each sample uses an independent task uuid + Pi sidecar process; "
                        "acgrip is a real site, raise with care if rate-limited.")
    return p.parse_args()


def find_latest(root: Path, prefix: str) -> Path:
    candidates = sorted(root.glob(f"{prefix}*"), reverse=True)
    if not candidates:
        raise FileNotFoundError(f"no {prefix}* under {root}")
    return candidates[0]


def load_sample_artifacts(sample_substr: str, bridge_root: Path, stage1_root: Path) -> dict[str, Any]:
    """加载样本的 stage-1 + stage-2 产物。"""
    # stage-2：找含 sample_substr 且 status=accepted 的产物
    stage2_file = None
    for f in bridge_root.glob("*.json"):
        if "progress" in f.name or "summary" in f.name:
            continue
        if sample_substr.lower() not in f.name.lower():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("status") == "accepted":
            stage2_file = f
            stage2_data = d
            break
    if stage2_file is None:
        raise FileNotFoundError(
            f"no accepted stage-2 artifact matching {sample_substr!r} under {bridge_root}"
        )

    # stage-1：同名文件
    stage1_file = stage1_root / stage2_file.name
    if not stage1_file.exists():
        raise FileNotFoundError(f"stage-1 artifact not found: {stage1_file}")
    stage1_data = json.loads(stage1_file.read_text(encoding="utf-8"))

    return {
        "stage1_file": stage1_file,
        "stage1_data": stage1_data,
        "stage2_file": stage2_file,
        "stage2_data": stage2_data,
    }


def extract_sample_context(arts: dict[str, Any]) -> dict[str, Any]:
    """从 stage-1/stage-2 产物抽全部 map_to_tmdb mapping + TMDB 标题/年份/media_type。

    支持多 mapping（TV 整季 / 多电影）：收集每个 map_to_tmdb 的 (source_path,
    tmdb_legal_node)，不只 mappings[0]。media_type 从 legal node 前缀判
    （movie: -> movie，tv: -> tv），合成路径据此分叉。
    """
    stage1 = arts["stage1_data"]
    stage2 = arts["stage2_data"]

    cp = (stage1.get("snapshot") or {}).get("compiled_plan") or {}
    assignments = cp.get("assignments") or []
    if not assignments:
        raise ValueError("stage-1 compiled_plan has no assignments")

    brr = stage2.get("bridge_run_result") or {}
    vp = brr.get("verified_plan") or {}
    mappings = vp.get("mappings") or []
    if not mappings:
        raise ValueError("stage-2 verified_plan has no mappings")

    # 收集全部 map_to_tmdb 的 (source_path, legal_node)
    mapped: list[dict[str, str]] = []
    for m in mappings:
        if m.get("disposition") != "map_to_tmdb":
            continue
        nodes = m.get("tmdb_legal_node_ids") or []
        if not nodes:
            continue
        mapped.append({
            "source_path": m.get("source_path") or "",
            "tmdb_legal_node": nodes[0],
        })
    if not mapped:
        raise ValueError("stage-2 verified_plan has no map_to_tmdb mappings")

    # media_type：按 legal node 前缀多数判定（混 movie/tv 时按主体）
    movie_n = sum(1 for x in mapped if x["tmdb_legal_node"].startswith("movie:"))
    tv_n = len(mapped) - movie_n
    media_type = "movie" if movie_n > tv_n else "tv"

    lg = brr.get("tmdb_legal_graph") or {}
    candidates = lg.get("candidates") or []

    # 多 movie 合集：每个 movie:<id> 有独立 TMDB title/year（如空之境界 7 部剧场版）。
    # 建 id -> (title, year) 映射，供 movie 分支合成各自 target，避免塌缩成同名。
    movie_meta: dict[str, dict[str, Any]] = {}
    for c in candidates:
        cid = c.get("tmdb_id") or c.get("id")
        ctype = c.get("type") or c.get("media_type")
        if ctype == "movie" and cid is not None:
            movie_meta[f"movie:{cid}"] = {
                "title": c.get("display_title") or c.get("title") or "",
                "year": c.get("year"),
            }

    # 多 TV series 合集：一个样本可能映到多个 TMDB tv series（如 0099 P4 本篇
    # tv:46388 + P4 Golden tv:61465，两个 series 都用 S01E01 编号）。旧实现 tv 分支
    # 用单一 series title 生成 ep_name → P4 Golden 的 target 用了 P4 本篇 title →
    # 生成同名 target_file → missing_videos 重复（39=26 真实 + 13 同名撞）。
    # 按 tv series ref 建各自的 title/year，tv 分支按 node 所属 series 取各自 title。
    tv_meta: dict[str, dict[str, Any]] = {}
    for c in candidates:
        ctype = c.get("type") or c.get("media_type")
        ref = c.get("tmdb_ref") or ""
        if ctype == "tv" and ref.startswith("tv:"):
            tv_meta[ref] = {
                "title": c.get("display_title") or c.get("title") or "",
                "year": c.get("year"),
            }

    # series title/year 必须取 verified_plan **实际映的** candidate，而非
    # candidates[0]（legal_graph 候选顺序未必等于实际映的）。0002 大和号2205
    # 曾因 candidates[0]=movie:860104(前章) 而实际映 tv:157583，导致 synthesize
    # 用前章 title 合成 series_root，"前章目录装 8 集"假错位。
    # 按 mapped 的 tmdb_legal_node 前缀（tv:<id> / movie:<id>）查候选，取其
    # display_title/year。tv 主体优先；纯 movie 取第一个 movie 映射。
    cand_by_ref: dict[str, dict[str, Any]] = {}
    for c in candidates:
        ref = c.get("tmdb_ref") or ""
        if ref:
            cand_by_ref[ref] = c
    chosen_cand: dict[str, Any] = {}
    for item in mapped:
        node = item["tmdb_legal_node"]
        prefix = node.split(":")[0] + ":" + node.split(":")[1] if ":" in node else node
        # tv:85937:S01E01 -> tv:85937；movie:635302 -> movie:635302
        parts = node.split(":")
        ref_key = ":".join(parts[:2]) if len(parts) >= 2 else node
        c = cand_by_ref.get(ref_key)
        if c:
            chosen_cand = c
            break
    if not chosen_cand and candidates:
        chosen_cand = candidates[0]
    display_title = (
        chosen_cand.get("display_title")
        or chosen_cand.get("original_title")
        or chosen_cand.get("title")
        or ""
    )
    original_title = chosen_cand.get("original_title") or ""
    year = chosen_cand.get("year")

    # 多季覆盖：从 stage-1 assignments 的 target.bangumi_subject_id 抽
    # source_path -> subject_id 映射（auto_fetch 多季覆盖的搜索词来源）。
    # stage-1 assignment.target 直接含 bangumi_subject_id + media_kind（不是
    # rename_plan.items 的 bangumi_assignment.target，结构不同）。
    src2subj: dict[str, int] = {}
    subject_media_kind: dict[int, str] = {}
    for a in assignments:
        if a.get("disposition") != "map_to_bangumi":
            continue
        tgt = a.get("target") or {}
        sid = tgt.get("bangumi_subject_id")
        sp = a.get("source_path")
        if sid and sp:
            try:
                sid_int = int(sid)
            except (TypeError, ValueError):
                continue
            if sid_int <= 0:
                continue
            src2subj[sp] = sid_int
            mk = str(tgt.get("media_kind") or "")
            if mk:
                subject_media_kind.setdefault(sid_int, mk)

    return {
        "mapped": mapped,
        "display_title": display_title,
        "original_title": original_title,
        "year": year,
        "media_type": media_type,
        "movie_meta": movie_meta,
        "tv_meta": tv_meta,
        "stage2_file_name": arts["stage2_file"].name,
        # 多季覆盖：per-source BGM subject 映射 + subject media_kind
        "src2subj": src2subj,
        "subject_media_kind": subject_media_kind,
    }


def _parse_tv_legal_node(node: str) -> tuple[int, int] | None:
    """解析 tv:<id>:S<ss>E<ee> -> (season, episode)。失败返回 None。"""
    import re
    m = re.match(r"tv:\d+:S(\d+)E(\d+)", node or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def sanitize_component(value: str) -> str:
    import re
    cleaned = re.sub(r'[<>:"/\\|?*]', " ", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(" .")
    return cleaned or "Unknown"


def synthesize_targets(ctx: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """合成 Emby 风格目标路径（视频文件不创建）。

    返回 (target_root, targets) where targets = [{source_path, target_file, season, episode}].
    - movie：单文件 <root>/<Title> (<year>)/<Title> (<year>).mkv（多 movie 时每个 movie
      一个 target，但 smoke 用 series root 简化，movie 合成到同一 root 下按节点）。
    - tv：按 tv:<id>:S<ss>E<ee> 解析季集，<series_root>/<Title> (<year>)/Season <ss>/
      <Title> - S<ss>E<ee>.mkv，每个 mapping 一个 target。
    """
    is_movie = ctx["media_type"] == "movie"
    title = ctx["display_title"] or ctx["original_title"] or "Unknown"
    year = ctx["year"]
    title_with_year = f"{sanitize_component(title)} ({year})" if year else sanitize_component(title)
    safe_title = sanitize_component(title)

    if is_movie:
        base = cm.get_config("movie_path") or cm.get_config("anime_movie_path") or "data/smoke_movie"
        movie_meta = ctx.get("movie_meta") or {}
        targets: list[dict[str, str]] = []
        # 多 movie 合集（如空之境界 7 部剧场版）：每个 movie:<id> 有独立 TMDB
        # title/year，合成各自目录与文件名，避免塌缩成同名导致 missing 去重只剩 1。
        # 无 per-movie 元数据时回退到 series 级 display_title/year（单 movie 正常路径）。
        for item in ctx["mapped"]:
            node = item["tmdb_legal_node"]
            meta = movie_meta.get(node) or {}
            m_title = meta.get("title") or title
            m_year = meta.get("year") or year
            m_title_with_year = (
                f"{sanitize_component(m_title)} ({m_year})" if m_year
                else sanitize_component(m_title)
            )
            # 多 movie：每个 movie 一个独立子目录；单 movie（meta 同 series title）
            # 时 target_root 取该 movie 目录，与单 movie 行为一致。
            movie_root = str(Path(base) / m_title_with_year)
            targets.append({
                "source_path": item["source_path"],
                "target_file": str(Path(movie_root) / f"{m_title_with_year}.mkv"),
                "season": "",
                "episode": "",
            })
        # target_root 回传第一个 movie 的目录（smoke 只用于日志展示，不影响 missing）
        target_root = str(Path(base) / title_with_year)
        return target_root, targets

    # tv（含 mixed tv+movie：如 0091 鬼灭 44 TV + 1 剧场版。movie legal node
    # 在 tv 主体任务里也要合成 target，否则 missing_videos 漏掉剧场版，
    # Pi 看不到就不会选——曾误判"剧场版无帖"，实为 smoke 漏合成 target）。
    base = cm.get_config("tv_path") or cm.get_config("anime_path") or "data/smoke_series"
    movie_base = cm.get_config("movie_path") or cm.get_config("anime_movie_path") or "data/smoke_movie"
    series_root = str(Path(base) / title_with_year)
    movie_meta = ctx.get("movie_meta") or {}
    tv_meta = ctx.get("tv_meta") or {}
    targets = []
    for item in ctx["mapped"]:
        node = item["tmdb_legal_node"]
        # movie legal node（mixed 任务里的剧场版）：按 movie 规则合成独立目录
        if node.startswith("movie:"):
            meta = movie_meta.get(node) or {}
            m_title = meta.get("title") or title
            m_year = meta.get("year") or year
            m_title_with_year = (
                f"{sanitize_component(m_title)} ({m_year})" if m_year
                else sanitize_component(m_title)
            )
            movie_root = str(Path(movie_base) / m_title_with_year)
            targets.append({
                "source_path": item["source_path"],
                "target_file": str(Path(movie_root) / f"{m_title_with_year}.mkv"),
                "season": "",
                "episode": "",
            })
            continue
        parsed = _parse_tv_legal_node(node)
        if parsed is None:
            # 无法解析季集的 TV 节点，跳过（不应发生，legal node 格式固定）
            continue
        season, episode = parsed
        season_dir = f"Season {season:02d}" if season > 0 else "Season 00"
        # 多 TV series 合集：按 node 所属 series ref 查 tv_meta 取各自 title/year
        # （0099 P4 本篇 tv:46388 + P4 Golden tv:61465，都 S01E01，须用各自 title
        # 生成 target 否则同名撞 → missing_videos 重复）。tv_meta 无该 series 时
        # 回退单一 series title（单 series 样本不受影响）。
        parts = node.split(":")
        series_ref = ":".join(parts[:2]) if len(parts) >= 2 else node
        tv_m = tv_meta.get(series_ref) or {}
        ep_title = tv_m.get("title") or title
        ep_year = tv_m.get("year") or year
        ep_safe_title = sanitize_component(ep_title)
        ep_title_with_year = (
            f"{ep_safe_title} ({ep_year})" if ep_year else ep_safe_title
        )
        ep_series_root = str(Path(base) / ep_title_with_year)
        ep_name = f"{ep_safe_title} - S{season:02d}E{episode:02d}.mkv"
        targets.append({
            "source_path": item["source_path"],
            "target_file": str(Path(ep_series_root) / season_dir / ep_name),
            "season": str(season),
            "episode": str(episode),
        })
    return series_root, targets


def build_synthetic_task_record(
    smoke_uuid: str,
    ctx: dict[str, Any],
    target_root: str,
    targets: list[dict[str, str]],
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any], dict[str, str]]]]:
    """构造虚拟 task + record（按 season/movie 拆多 task，模拟生产多 task 落地）。

    返回 (main_task_data, all_tasks) where all_tasks = [(uuid, task_data, record_data), ...].
    主 task（index 0）含 bgm_subjects/bgm_video_subject_map，auto_fetch 读它做多季覆盖。
    其余 task 仅用于 processor 配对时提供正确的 per-season/per-movie target card
    （season_id / is_movie 字段），避免单 task 多季导致 target card season 全标 1
    的失真（曾使 Pi 误判 S02 TV "no matching target video"）。

    所有 task 的 target_root 统一设成 series_root（target_root），让 processor
    `_load_processed_tasks(target_root=...)` 能聚合全部 season task 的视频。
    movie task is_movie=True + video_targets 指向 movie 目录，target card is_movie 正确。
    """
    is_movie_overall = ctx["media_type"] == "movie"
    title = ctx["display_title"] or ctx["original_title"] or "Unknown"

    # 按 season/movie 分组 targets（season="" 是 movie）
    from collections import defaultdict
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for t in targets:
        groups[str(t.get("season") or "")].append(t)

    # 全量 record（source->target）用于 bgm_video_subject_map 反推
    full_record = {t["source_path"]: t["target_file"] for t in targets if t["source_path"]}

    # 多季覆盖：构造 bgm_video_subject_map（target_basename -> subject_id）+
    # bgm_subjects（每 subject id/name/name_cn/media_kind，auto_fetch Pi 据此按
    # subject 分组多帖多包搜字幕）。src2subj 来自 stage-1 assignments 的
    # target.bangumi_subject_id，按 record_data 的 source->target basename 反推。
    src2subj = ctx.get("src2subj") or {}
    subject_media_kind = ctx.get("subject_media_kind") or {}
    video_subject_map: dict[str, int] = {}
    for source_path, target_file in full_record.items():
        sid = src2subj.get(source_path)
        if not sid:
            continue
        target_basename = Path(target_file).name
        if target_basename:
            video_subject_map[target_basename] = sid
    subject_ids = sorted(set(video_subject_map.values()))
    bgm_subjects: list[dict[str, Any]] = []
    if subject_ids:
        try:
            from src.bangumi.client import BangumiClient

            bgm_client = BangumiClient()
            for sid in subject_ids:
                s_name = ""
                s_name_cn = ""
                try:
                    subj = bgm_client.get_subject(sid)
                    if subj is not None:
                        s_name = str(subj.name or "")
                        s_name_cn = str(subj.name_cn or "")
                except Exception as exc:
                    print(f"[smoke] WARN: Bangumi subject {sid} 查询失败: {exc}")
                bgm_subjects.append({
                    "id": sid,
                    "name": s_name,
                    "name_cn": s_name_cn,
                    "media_kind": subject_media_kind.get(sid, ""),
                })
        except Exception as exc:
            print(f"[smoke] WARN: BangumiClient 初始化失败，bgm_subjects 名为空: {exc}")
            bgm_subjects = [
                {"id": sid, "name": "", "name_cn": "",
                 "media_kind": subject_media_kind.get(sid, "")}
                for sid in subject_ids
            ]
    # 兼容旧单值字段（主体 subject = assignment 数最多）
    main_bgm_name = ""
    main_bgm_name_cn = ""
    if bgm_subjects:
        from collections import Counter
        subj_counts = Counter(video_subject_map.values())
        if subj_counts:
            main_sid = min(subj_counts, key=lambda s: (-subj_counts[s], s))
            main = next((s for s in bgm_subjects if s["id"] == main_sid), {})
            main_bgm_name = main.get("name", "")
            main_bgm_name_cn = main.get("name_cn", "")

    # 按 season 构造多 task。排序：非空 season 升序在前，"" (movie) 最后。
    def _group_sort_key(season_str: str) -> tuple:
        if season_str == "":
            return (1, 0)
        try:
            return (0, int(season_str))
        except ValueError:
            return (0, 999)

    all_tasks: list[tuple[str, dict[str, Any], dict[str, str]]] = []
    main_task_data: dict[str, Any] | None = None
    for idx, season_str in enumerate(sorted(groups.keys(), key=_group_sort_key)):
        group_targets = groups[season_str]
        group_is_movie = season_str == ""
        # 主 task 用 smoke_uuid（auto_fetch 读 bgm_subjects），其余用子 uuid
        if group_is_movie:
            task_uuid = smoke_uuid if idx == 0 else f"{smoke_uuid}-movie"
        else:
            task_uuid = smoke_uuid if idx == 0 else f"{smoke_uuid}-S{season_str}"
        season_id = 0 if group_is_movie else (int(season_str) if season_str else 1)
        task_data = {
            "uuid": task_uuid,
            "type": "rename",
            "name": title,
            "tmdb_name": title,
            "original_name": ctx["original_title"],
            "path": group_targets[0]["source_path"] if group_targets else "",
            "is_movie": group_is_movie,
            "season_id": season_id,
            "target_root": target_root,  # 统一 series_root 让 processor 聚合
            "status": "completed",
        }
        # movie task 加 video_targets（电影独立目录），target card is_movie 正确
        if group_is_movie:
            video_targets = {}
            for t in group_targets:
                bn = Path(t["target_file"]).name
                video_targets[bn] = t["target_file"]
            task_data["video_targets"] = video_targets
        group_record = {t["source_path"]: t["target_file"] for t in group_targets if t["source_path"]}
        # 每个 task 都写 bgm_video_subject_map（该 task 的 video 子集）+ bgm_subjects
        # （全量，供 processor 反查 arc 名）。processor 加载各 season task 时读自己
        # task_data 的映射填 target card arc_name，让字幕 Case Agent 区分同 episode
        # 不同 season（S02E01 無限列車編 vs S03E01 遊郭編）。
        group_video_subject_map: dict[str, int] = {}
        for t in group_targets:
            bn = Path(t["target_file"]).name
            sid = video_subject_map.get(bn)
            if sid:
                group_video_subject_map[bn] = sid
        # 主 task（idx==0, auto_fetch process_task_mapping 只读主 task_data）
        # 写**全量** video_subject_map：auto_fetch 用 missing_videos_override 传入
        # 全 45 个视频，若主 task 只写本 season 子集，非本 season 视频 sid=0，
        # Pi 无法按 subject 分组搜帖（曾导致 0091 S02/S03/剧场版 sid=0 全部漏搜，
        # Pi 反去搜不在 missing 的 S04/S05 arc）。子 task 仍写各自 group 子集，
        # 供 processor 按 season 加载 target card 用。
        if idx == 0:
            task_data["bgm_video_subject_map"] = dict(video_subject_map)
        else:
            task_data["bgm_video_subject_map"] = group_video_subject_map
        task_data["bgm_subjects"] = bgm_subjects
        # 主 task 额外写兼容单值字段（auto_fetch 读主体 subject 名）
        if idx == 0:
            task_data["bgm_subject_name"] = main_bgm_name
            task_data["bgm_subject_name_cn"] = main_bgm_name_cn
            task_data["bgm_subject_ids"] = subject_ids
            main_task_data = task_data
        all_tasks.append((task_uuid, task_data, group_record))

    # 兜底：targets 为空时构造空主 task
    if main_task_data is None:
        season_id = 0 if is_movie_overall else 1
        main_task_data = {
            "uuid": smoke_uuid, "type": "rename", "name": title,
            "tmdb_name": title, "original_name": ctx["original_title"],
            "path": "", "is_movie": is_movie_overall, "season_id": season_id,
            "target_root": target_root, "status": "completed",
            "bgm_video_subject_map": video_subject_map, "bgm_subjects": bgm_subjects,
            "bgm_subject_name": main_bgm_name, "bgm_subject_name_cn": main_bgm_name_cn,
            "bgm_subject_ids": subject_ids,
        }
        all_tasks.append((smoke_uuid, main_task_data, {}))
    return main_task_data, all_tasks


def persist_synthetic(
    smoke_uuid: str,
    all_tasks: list[tuple[str, dict[str, Any], dict[str, str]]],
) -> None:
    """写多 task（按 season 拆）+ 各自 record。主 task uuid = smoke_uuid。"""
    for task_uuid, task_data, record_data in all_tasks:
        write_task(task_uuid, task_data)
        record_file = RECORD_PATH / f"{task_uuid}.json"
        record_file.parent.mkdir(parents=True, exist_ok=True)
        record_file.write_text(json.dumps(record_data, ensure_ascii=False, indent=4), encoding="utf-8")


def cleanup_synthetic(smoke_uuid: str, all_task_uuids: list[str] | None = None) -> None:
    """清理多 task（主 + 子 season/movie task）+ record + mapping + 下载目录。"""
    uuids = list(all_task_uuids) if all_task_uuids else [smoke_uuid]
    # 兜底含主 uuid
    if smoke_uuid not in uuids:
        uuids.append(smoke_uuid)
    for task_uuid in uuids:
        for p in (TASK_PATH / f"{task_uuid}.json",
                  RECORD_PATH / f"{task_uuid}.json",
                  TASK_PATH / f"{task_uuid}.subtitle_fetch_mapping.json"):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    # 清理临时下载目录（按主 uuid）
    from src.utils.path import SUBTITLE_UPLOAD_PATH
    dl_dir = SUBTITLE_UPLOAD_PATH / "auto_fetch_mapping" / smoke_uuid
    if dl_dir.exists():
        shutil.rmtree(dl_dir, ignore_errors=True)


def run_one(
    sample_substr: str,
    args: argparse.Namespace,
    bridge_root: Path,
    stage1_root: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """跑单个样本的 mapping smoke，返回结果摘要 dict。"""
    print(f"\n{'=' * 70}")
    print(f"[smoke] sample substring = {sample_substr!r}")
    try:
        arts = load_sample_artifacts(sample_substr, bridge_root, stage1_root)
    except FileNotFoundError as exc:
        print(f"[smoke] SKIP: {exc}")
        return {"sample": sample_substr, "status": "skip", "reason": str(exc),
                "exit": 1}

    ctx = extract_sample_context(arts)
    print(f"[smoke] sample = {ctx['stage2_file_name']}")
    print(f"[smoke] mapped count = {len(ctx['mapped'])}")
    for i, m in enumerate(ctx["mapped"][:5]):
        print(f"  [{i}] {m['tmdb_legal_node']} <- {m['source_path'][:55]}")
    if len(ctx["mapped"]) > 5:
        print(f"  ... and {len(ctx['mapped']) - 5} more")
    print(f"[smoke] display_title = {ctx['display_title']!r} year = {ctx['year']} media = {ctx['media_type']}")

    target_root, targets = synthesize_targets(ctx)
    print(f"[smoke] synthesized target_root = {target_root}")
    print(f"[smoke] synthesized {len(targets)} target(s):")
    for t in targets[:5]:
        print(f"  - {t['source_path'][:40]} -> {t['target_file']}")
    if len(targets) > 5:
        print(f"  ... and {len(targets) - 5} more")

    smoke_uuid = f"smoke-af-{uuid.uuid4().hex[:8]}"
    main_task_data, all_tasks = build_synthetic_task_record(
        smoke_uuid, ctx, target_root, targets
    )
    persist_synthetic(smoke_uuid, all_tasks)
    all_task_uuids = [t_uuid for t_uuid, _, _ in all_tasks]
    print(f"[smoke] synthetic task/record uuid = {smoke_uuid} ({len(all_tasks)} task(s): {all_task_uuids})")

    fetcher = SubtitleAutoFetcher()
    missing_override = [Path(t["target_file"]) for t in targets]

    print("[smoke] running process_task_mapping (acgrip real site) ...")
    try:
        result = fetcher.process_task_mapping(
            smoke_uuid, missing_videos_override=missing_override
        )
    except Exception as exc:
        print(f"[smoke] ERROR: {exc!r}")
        result = {"status": "error", "error": str(exc)}
    finally:
        mapping_record = TASK_PATH / f"{smoke_uuid}.subtitle_fetch_mapping.json"
        if mapping_record.exists():
            shutil.copy2(mapping_record, out_dir / ctx["stage2_file_name"])

    print("\n=== smoke result ===")
    print(f"status = {result.get('status')}")
    print(f"reason = {result.get('reason')}")
    print(f"case_agent_status = {result.get('case_agent_status')}")
    selections_count = result.get("selections_count")
    if selections_count is not None:
        print(f"selections_count = {selections_count}")
        selections = result.get("selections") or []
        for sel in selections:
            sid = sel.get("bangumi_subject_id") or "-"
            s_status = sel.get("status")
            cand = sel.get("selected_candidate") or {}
            pkg = sel.get("selected_package") or {}
            print(f"  [sel#{sel.get('index')}] subject={sid} status={s_status} "
                  f"cand={cand.get('title')} pkg={pkg.get('package_id')} "
                  f"flags={list(pkg.get('package_flags') or [])}")
    # 多 selection 合并的顶层 mapping 产物
    mappings = result.get("mappings") or []
    unmatched = result.get("unmatched") or []
    no_target = result.get("no_target_videos") or []

    # ------------------------------------------------------------------
    # 主展示口径：VIDEO 维度（落地视频是否匹配上字幕）
    # 基准 = smoke 合成的总落地视频 targets（= rename 链路 map_to_tmdb 的 TMDB 合法落点）。
    # 这是用户真正关心的："实际落地的 video 都尽量有字幕"。
    # 字幕维度的 unmatched/no_target 降级为次要审计信息（见下方）。
    # ------------------------------------------------------------------
    from collections import defaultdict
    video_langs: dict[str, set[str]] = defaultdict(set)
    for m in mappings:
        v = m.get("video")
        lang = m.get("language")
        if v and lang:
            video_langs[v].add(lang)
    covered_videos = set(video_langs.keys())
    total_videos = len(targets)
    covered_count = len(covered_videos)
    has_zhcn = sum(1 for langs in video_langs.values() if "zh-CN" in langs)
    uncovered = [
        Path(t["target_file"]).name
        for t in targets
        if Path(t["target_file"]).name not in covered_videos
    ]
    print(f"\n[video 维度] 总落地视频 = {total_videos}")
    pct = covered_count * 100 // total_videos if total_videos else 0
    print(f"  已配对字幕 = {covered_count} ({pct}%)")
    print(f"  有 zh-CN 字幕 = {has_zhcn}")
    print(f"  缺字幕视频 = {len(uncovered)}")
    if uncovered:
        print(f"  缺字幕列表 (前 10): {uncovered[:10]}")

    # 次要审计：字幕维度的未用字幕（duplicate_language/no_confident_match 等待人工 +
    # no_target_video 特典无落点）。processor 内部产物，非主展示口径。
    print(f"\n[字幕维度·审计] matched={len(mappings)} unmatched(待人工)={len(unmatched)} "
          f"no_target(特典无落点)={len(no_target)}")
    if unmatched:
        print(f"  unmatched (前 5): {[(u.get('reason_kind'), (u.get('subtitle') or u.get('archive_path') or '')[:40]) for u in unmatched[:5]]}")

    out_file = out_dir / ctx["stage2_file_name"]
    print(f"\n[smoke] artifact -> {out_file}")

    if not args.keep_task:
        cleanup_synthetic(smoke_uuid, all_task_uuids)
        print("[smoke] cleaned up synthetic task/record")
    else:
        print(f"[smoke] kept synthetic task/record uuid = {smoke_uuid}")

    status = str(result.get("status") or "error")
    # 收集所有 selection 的 package flags（多 selection 合并）
    all_pkg_flags: list[str] = []
    for sel in (result.get("selections") or []):
        pkg = sel.get("selected_package") or {}
        for f in (pkg.get("package_flags") or []):
            if f not in all_pkg_flags:
                all_pkg_flags.append(f)
    return {
        "sample": sample_substr,
        "stage2_file": ctx["stage2_file_name"],
        "status": status,
        "reason": result.get("reason"),
        "case_agent_status": result.get("case_agent_status"),
        "selections_count": result.get("selections_count"),
        # video 维度（主口径）
        "total_videos": total_videos,
        "covered_videos": covered_count,
        "has_zhcn_videos": has_zhcn,
        "uncovered_videos": len(uncovered),
        # 字幕维度（审计）
        "matched": len(mappings),
        "unmatched": len(unmatched),
        "no_target": len(no_target),
        "selected_package_flags": all_pkg_flags,
        "exit": 0 if status in ("success", "accepted", "skipped", "fail_closed") else 1,
    }


def main() -> int:
    args = parse_args()

    bridge_root = Path(args.bridge_root) if args.bridge_root else find_latest(
        GENERATED_ROOT, "bgm_to_tmdb_bridge_gate_"
    )
    stage1_root = Path(args.stage1_root) if args.stage1_root else find_latest(
        GENERATED_ROOT, "local_bangumi_mapping_gate_"
    )
    print(f"[smoke] bridge_root = {bridge_root}")
    print(f"[smoke] stage1_root = {stage1_root}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir) if args.output_dir else (
        GENERATED_ROOT / f"auto_fetch_mapping_smoke_{ts}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    samples_str = str(args.samples or "").strip()
    if samples_str:
        sample_list = [s.strip() for s in samples_str.split(",") if s.strip()]
    else:
        sample_list = [args.sample]
    print(f"[smoke] samples to run: {sample_list}")
    print(f"[smoke] workers = {args.workers}")

    def _run_safe(sample_substr: str) -> dict[str, Any]:
        """run_one 包装：捕获异常，并发下保证返回完整 result dict。"""
        try:
            return run_one(sample_substr, args, bridge_root, stage1_root, out_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"[smoke] run_one ERROR ({sample_substr}): {exc!r}")
            return {"sample": sample_substr, "status": "error",
                    "reason": str(exc), "exit": 1}

    results: list[dict[str, Any]] = []
    worker_count = max(1, int(args.workers or 1))
    if len(sample_list) == 1 or worker_count == 1:
        # 串行：保留 batch N/M 进度打印
        for idx, s in enumerate(sample_list, 1):
            print(f"\n[smoke] === batch {idx}/{len(sample_list)} ===")
            r = _run_safe(s)
            r.setdefault("sample", s)
            results.append(r)
    else:
        # 并发：每个样本独立 task uuid + Pi sidecar，acgrip 真站点低并发
        indexed: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_run_safe, s): idx
                for idx, s in enumerate(sample_list)
            }
            for future in as_completed(futures):
                idx = futures[future]
                r = future.result()
                r.setdefault("sample", sample_list[idx])
                indexed[idx] = r
                print(f"[smoke] completed {r.get('sample')} "
                      f"-> status={r.get('status')} "
                      f"case_agent={r.get('case_agent_status')} "
                      f"video {r.get('covered_videos')}/{r.get('total_videos')} "
                      f"缺字幕={r.get('uncovered_videos')}")
        results = [indexed[i] for i in range(len(sample_list))]

    print("\n" + "=" * 78)
    print("[smoke] BATCH SUMMARY（主口径 = video 维度：落地视频覆盖率）")
    print("=" * 78)
    # 主口径 video 维度：covered/total = 已配字幕落地视频 / 总落地视频；
    # uncovered = 缺字幕视频数（真正待人工/待补抓）。sel = Pi 多 selection 数。
    # 字幕维度 unmatched/no_target 降级为审计列（duplicate/特典无落点，非配对质量）。
    print(f"{'sample':<10} {'status':<12} {'sel':<5} {'video 覆盖':<14} {'缺字幕':<8} {'zh-CN':<8} {'unmatched':<10} reason")
    for r in results:
        total = r.get("total_videos") or 0
        covered = r.get("covered_videos") or 0
        cov_str = f"{covered}/{total}" if total else "-"
        print(f"{r.get('sample',''):<10} {str(r.get('status','')):<12} "
              f"{str(r.get('selections_count') or '-'):<5} "
              f"{cov_str:<14} "
              f"{str(r.get('uncovered_videos','')):<8} "
              f"{str(r.get('has_zhcn_videos','')):<8} "
              f"{str(r.get('unmatched','')):<10} "
              f"{r.get('reason') or ''}")
    print("=" * 78)
    print(f"[smoke] artifacts -> {out_dir}")

    return 0 if all(r.get("exit") == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
