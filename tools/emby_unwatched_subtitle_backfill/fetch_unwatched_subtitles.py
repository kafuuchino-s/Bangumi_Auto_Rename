from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config.config_manager import cm
from src.logger import logger
from src.notification.emby_notify import get_emby_notifier
from src.rename.utils import VIDEO_SUFFIX
from src.subtitle.auto_fetch import SubtitleAutoFetcher
from src.utils.path import RECORD_PATH
from src.utils.utils import get_record, get_task


@dataclass
class EmbyUser:
    user_id: str
    name: str


@dataclass
class SelectedTask:
    task_uuid: str
    priority: int
    priority_name: str
    matched_items: List[str] = field(default_factory=list)
    matched_paths: List[str] = field(default_factory=list)


class EmbyClient:
    def __init__(self, host: str, api_key: str) -> None:
        self.host = self._normalize_host(host)
        self.api_key = api_key
        self.session = requests.Session()

    @staticmethod
    def _normalize_host(host: str) -> str:
        host = (host or "").strip().rstrip("/")
        if host and not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        return host

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        merged_params = {"api_key": self.api_key}
        if params:
            merged_params.update(params)

        response = self.session.get(
            f"{self.host}{path}",
            params=merged_params,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def list_users(self) -> List[EmbyUser]:
        data = self._get("/Users")
        if not isinstance(data, list):
            return []

        users: List[EmbyUser] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            policy = item.get("Policy") or {}
            if isinstance(policy, dict) and policy.get("IsDisabled"):
                continue

            user_id = str(item.get("Id") or "").strip()
            name = str(item.get("Name") or "").strip()
            if not user_id:
                continue
            users.append(EmbyUser(user_id=user_id, name=name or user_id))
        return users

    def resolve_user(self, user_id: Optional[str] = None) -> EmbyUser:
        users = self.list_users()
        if not users:
            raise RuntimeError("未从 Emby 获取到可用用户")

        if user_id:
            target = str(user_id).strip()
            for user in users:
                if user.user_id == target or user.name.casefold() == target.casefold():
                    return user
            raise RuntimeError(f"未找到指定 Emby 用户: {target}")

        preferred_name = "kafuuchino"
        for user in users:
            if user.name.casefold() == preferred_name:
                return user

        return users[0]

    def list_resume_items(self, user_id: str, limit: int) -> List[Dict[str, Any]]:
        data = self._get(
            f"/Users/{user_id}/Items/Resume",
            params={
                "Limit": max(1, int(limit)),
                "Fields": "Path",
            },
        )
        items = data.get("Items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    def list_unplayed_items(self, user_id: str, limit: int) -> List[Dict[str, Any]]:
        data = self._get(
            f"/Users/{user_id}/Items",
            params={
                "Recursive": "true",
                "IncludeItemTypes": "Episode,Movie",
                "Filters": "IsUnplayed",
                "Fields": "Path",
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
                "Limit": max(1, int(limit)),
            },
        )
        items = data.get("Items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="优先给 Emby 未观看的 BAR 媒体补字幕。"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只输出命中结果与执行顺序，不实际抓字幕",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="最终最多执行多少个 BAR 任务（默认 10）",
    )
    parser.add_argument(
        "--resume-limit",
        type=int,
        default=20,
        help="从 Emby 继续观看列表最多读取多少项（默认 20）",
    )
    parser.add_argument(
        "--unplayed-limit",
        type=int,
        default=50,
        help="从 Emby 未播放列表最多读取多少项（默认 50）",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default="",
        help="指定 Emby 用户 ID；不传则自动选默认主用户",
    )
    return parser.parse_args()


def _normalize_windows_path(path: str) -> str:
    normalized = os.path.normpath((path or "").strip().replace("/", "\\"))
    return os.path.normcase(normalized)


def _normalize_posix_path(path: str) -> str:
    normalized = os.path.normpath((path or "").strip().replace("\\", "/"))
    return normalized.replace("\\", "/").casefold()


def _host_to_docker_path(
    path: str,
    host_prefix: str,
    docker_mnt: str,
) -> str:
    raw = (path or "").strip()
    if not raw or not host_prefix or not docker_mnt:
        return ""

    host_prefix = host_prefix.rstrip("\\/")
    docker_mnt = docker_mnt.rstrip("/")
    if not raw.lower().startswith(host_prefix.lower()):
        return ""

    relative = raw[len(host_prefix) :].replace("\\", "/")
    return f"{docker_mnt}{relative}"


def _docker_to_host_path(
    path: str,
    host_prefix: str,
    docker_mnt: str,
) -> str:
    raw = (path or "").strip().replace("\\", "/")
    if not raw or not host_prefix or not docker_mnt:
        return ""

    host_prefix = host_prefix.rstrip("\\/")
    docker_mnt = docker_mnt.rstrip("/")
    if not raw.casefold().startswith(docker_mnt.casefold()):
        return ""

    relative = raw[len(docker_mnt) :].replace("/", "\\")
    return f"{host_prefix}{relative}"


def build_path_keys(path: str, host_prefix: str, docker_mnt: str) -> set[str]:
    candidates = {
        str(path or "").strip(),
    }

    host_to_docker = _host_to_docker_path(path, host_prefix, docker_mnt)
    if host_to_docker:
        candidates.add(host_to_docker)

    docker_to_host = _docker_to_host_path(path, host_prefix, docker_mnt)
    if docker_to_host:
        candidates.add(docker_to_host)

    keys: set[str] = set()
    for candidate in list(candidates):
        if not candidate:
            continue
        keys.add(f"win:{_normalize_windows_path(candidate)}")
        keys.add(f"posix:{_normalize_posix_path(candidate)}")
    return keys


def is_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_SUFFIX


def build_bar_task_index(host_prefix: str, docker_mnt: str) -> Dict[str, str]:
    path_to_task: Dict[str, str] = {}
    if not RECORD_PATH.exists():
        return path_to_task

    record_files = sorted(
        RECORD_PATH.glob("*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )

    for record_file in record_files:
        task_uuid = record_file.stem
        task_data = get_task(task_uuid)
        if not task_data or task_data.get("type") == "subtitle":
            continue

        record_data = get_record(task_uuid)
        if not isinstance(record_data, dict):
            continue

        for target in record_data.values():
            if not isinstance(target, str) or not is_video_path(target):
                continue
            for key in build_path_keys(target, host_prefix, docker_mnt):
                path_to_task.setdefault(key, task_uuid)

    return path_to_task


def filter_playable_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = str(item.get("Path") or "").strip()
        if not path or not is_video_path(path):
            continue
        filtered.append(item)
    return filtered


def choose_tasks(
    resume_items: List[Dict[str, Any]],
    unplayed_items: List[Dict[str, Any]],
    path_to_task: Dict[str, str],
    host_prefix: str,
    docker_mnt: str,
) -> tuple[List[SelectedTask], int, int]:
    selected: Dict[str, SelectedTask] = {}
    matched_items = 0
    skipped_non_bar_items = 0

    for priority, priority_name, items in (
        (0, "resume", resume_items),
        (1, "unplayed", unplayed_items),
    ):
        for item in items:
            item_path = str(item.get("Path") or "").strip()
            task_uuid = None
            for key in build_path_keys(item_path, host_prefix, docker_mnt):
                task_uuid = path_to_task.get(key)
                if task_uuid:
                    break

            if not task_uuid:
                skipped_non_bar_items += 1
                continue

            matched_items += 1
            item_name = str(
                item.get("SeriesName")
                or item.get("Name")
                or item.get("Id")
                or task_uuid
            ).strip()
            current = selected.get(task_uuid)
            if current is None:
                current = SelectedTask(
                    task_uuid=task_uuid,
                    priority=priority,
                    priority_name=priority_name,
                )
                selected[task_uuid] = current
            elif priority < current.priority:
                current.priority = priority
                current.priority_name = priority_name

            if item_name and item_name not in current.matched_items:
                current.matched_items.append(item_name)
            if item_path and item_path not in current.matched_paths:
                current.matched_paths.append(item_path)

    ordered = sorted(
        selected.values(),
        key=lambda item: (
            item.priority,
            item.task_uuid,
        ),
    )
    return ordered, matched_items, skipped_non_bar_items


def validate_task_for_fetch(task_uuid: str) -> Optional[str]:
    task_data = get_task(task_uuid)
    if not task_data:
        return "task_not_found"
    if task_data.get("type") == "subtitle":
        return "subtitle_task_not_supported"

    record_data = get_record(task_uuid)
    if not isinstance(record_data, dict) or not record_data:
        return "record_not_found"

    for target in record_data.values():
        if not isinstance(target, str) or not is_video_path(target):
            continue
        if Path(target).exists():
            return None
    return "target_video_missing"


def load_emby_client() -> EmbyClient:
    host = str(cm.get_config("emby_host") or "").strip()
    api_key = str(cm.get_config("emby_api_key") or "").strip()
    if not host:
        raise RuntimeError("未配置 emby_host")
    if not api_key:
        raise RuntimeError("未配置 emby_api_key")
    return EmbyClient(host=host, api_key=api_key)


def run() -> int:
    args = parse_args()
    limit = max(1, int(args.limit))
    resume_limit = max(1, int(args.resume_limit))
    unplayed_limit = max(1, int(args.unplayed_limit))
    host_prefix = str(cm.get_config("host_path_prefix") or "").strip()
    docker_mnt = str(cm.get_config("docker_mnt") or "/media").strip()

    logger.info("[Emby补字幕] 开始执行未观看优先补字幕脚本")
    logger.info(
        "[Emby补字幕] 参数: dry_run=%s, limit=%s, resume_limit=%s, unplayed_limit=%s",
        args.dry_run,
        limit,
        resume_limit,
        unplayed_limit,
    )

    client = load_emby_client()
    user = client.resolve_user(args.user_id or None)
    logger.info("[Emby补字幕] 目标用户: %s (%s)", user.name, user.user_id)

    resume_items = filter_playable_items(client.list_resume_items(user.user_id, resume_limit))
    unplayed_items = filter_playable_items(
        client.list_unplayed_items(user.user_id, unplayed_limit)
    )
    logger.info(
        "[Emby补字幕] Emby 返回 Resume=%s, Unplayed=%s",
        len(resume_items),
        len(unplayed_items),
    )

    path_to_task = build_bar_task_index(host_prefix, docker_mnt)
    logger.info("[Emby补字幕] 已建立 BAR 路径索引: %s 条", len(path_to_task))

    selected_tasks, matched_items, skipped_non_bar_items = choose_tasks(
        resume_items,
        unplayed_items,
        path_to_task,
        host_prefix,
        docker_mnt,
    )
    selected_tasks = selected_tasks[:limit]

    logger.info(
        "[Emby补字幕] 匹配到 BAR 项目=%s, 非 BAR 项目跳过=%s, 最终任务数=%s",
        matched_items,
        skipped_non_bar_items,
        len(selected_tasks),
    )

    for index, selected in enumerate(selected_tasks, 1):
        logger.info(
            "[Emby补字幕] #%s priority=%s task_uuid=%s items=%s",
            index,
            selected.priority_name,
            selected.task_uuid,
            ", ".join(selected.matched_items[:3]),
        )

    if args.dry_run:
        logger.info("[Emby补字幕] dry-run 模式，不执行实际抓取")
        return 0

    fetcher = SubtitleAutoFetcher()
    success_count = 0
    skipped_count = 0
    failed_count = 0

    for index, selected in enumerate(selected_tasks, 1):
        task_uuid = selected.task_uuid
        invalid_reason = validate_task_for_fetch(task_uuid)
        if invalid_reason:
            failed_count += 1
            logger.warning(
                "[Emby补字幕] #%s 跳过 task=%s, reason=%s",
                index,
                task_uuid,
                invalid_reason,
            )
            continue

        logger.info(
            "[Emby补字幕] #%s 开始处理 task=%s, priority=%s",
            index,
            task_uuid,
            selected.priority_name,
        )
        try:
            result = fetcher.process_task(task_uuid)
        except Exception as exc:
            failed_count += 1
            logger.exception(
                "[Emby补字幕] task=%s 执行异常: %s",
                task_uuid,
                exc,
            )
            continue

        status = str(result.get("status") or "failed").strip().lower()
        if status == "success":
            success_count += 1
            logger.info("[Emby补字幕] task=%s 抓取成功", task_uuid)
        elif status == "skipped":
            skipped_count += 1
            logger.info(
                "[Emby补字幕] task=%s 已跳过, reason=%s",
                task_uuid,
                result.get("reason"),
            )
        else:
            failed_count += 1
            logger.warning(
                "[Emby补字幕] task=%s 抓取失败, reason=%s",
                task_uuid,
                result.get("reason"),
            )

    logger.info(
        "[Emby补字幕] 执行完成: success=%s, skipped=%s, failed=%s",
        success_count,
        skipped_count,
        failed_count,
    )

    if success_count > 0:
        notifier = get_emby_notifier()
        ok, message = notifier.refresh_library()
        if ok:
            logger.info("[Emby补字幕] 已通知 Emby 刷新媒体库: %s", message)
        else:
            logger.warning("[Emby补字幕] Emby 刷新失败: %s", message)

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
