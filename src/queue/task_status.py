"""任务状态枚举和数据模型"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TaskStatus(Enum):
    """任务状态枚举"""

    PENDING = "pending"  # 队列中等待
    RUNNING = "running"  # 正在执行
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败


@dataclass
class QueuedTask:
    """队列中的任务数据模型"""

    task_id: str  # 队列任务唯一ID
    path: str  # 文件/文件夹路径
    is_anime: Optional[bool] = None  # 是否为动漫
    is_movie: Optional[bool] = None  # 是否为电影
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None

    # 用于重试场景的原始UUID
    original_uuid: Optional[str] = None

    # 用于编辑页面的自定义参数
    cus_name: Optional[str] = None
    cus_season_id: Optional[int] = None

    # AI设置（用于编辑页面临时禁用AI）
    use_ai: Optional[bool] = None

    # 是否为子任务（由父任务拆分出来的，不会再拆分）
    is_sub_task: bool = False
