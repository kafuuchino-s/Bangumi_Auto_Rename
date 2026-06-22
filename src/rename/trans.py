import os
import json
import shutil
from typing import Dict, Optional
from pathlib import Path

from ..logger import logger
from ..utils.path import RECORD_PATH
from ..config.config_manager import cm


# overwrite_existing 策略归一化。config 历史上是 bool（True=覆盖/False=拒绝），
# 现改为两态字符串：'覆盖' / '跳过'。兼容旧 bool 值：
#   True  → '覆盖'（目标已存在删旧重落）
#   False → '跳过'（目标已存在跳过该文件，不失败不回滚——替代旧"拒绝"语义，
#           因为"整任务失败回滚"在实际使用中无意义，用户要么覆盖要么跳过）
def _normalize_overwrite_policy(value) -> str:
    if isinstance(value, bool):
        return '覆盖' if value else '跳过'
    if value == '覆盖':
        return '覆盖'
    # '跳过' 或任何其他值（含 None/空）默认跳过（更安全，不误删已落盘文件）
    return '跳过'


class Trans:
    def __init__(
        self,
        R: Dict[Path, Path],
        uuid: str,
        force_mode: Optional[str] = None,
        force_overwrite: Optional[bool] = None,
        write_record: bool = True,
    ) -> None:
        """
        初始化文件传输器

        Args:
            R: 源文件到目标文件的映射
            uuid: 任务ID
            force_mode: 强制使用的模式（复制/剪切/链接），None则使用配置
            force_overwrite: 强制覆盖策略（'覆盖'/'跳过'，兼容旧 bool），None则使用配置
            write_record: 是否写入 record 文件（data/record/{uuid}.json）
        """
        self.mode = force_mode or cm.get_config('mode')
        self.overwrite = _normalize_overwrite_policy(
            force_overwrite
            if force_overwrite is not None
            else cm.get_config('overwrite_existing')
        )
        self.R = R
        self.uuid = uuid
        self.write_record = write_record
        # 实际落地映射 {源:目标}（排除跳过的），供上层统计真实入库数 / 通知。
        # 历史上 record 直接 dump 原始 R，跳过模式下会把"没落地"伪装成"已入库"，
        # 导致 transferred_file_count 虚高、TG「已入库 N 个文件」与实际落地不符。
        self.landed_mapping: Dict[Path, Path] = {}
        # 跳过映射 {源:目标}（目标已存在且策略=跳过，本次未落地），供上层通知
        # 如实显示「跳过入库 N 个文件」而非隐瞒跳过 / 虚报已入库。
        self.skipped_mapping: Dict[Path, Path] = {}

    def trans_file(self):
        path = RECORD_PATH / f'{self.uuid}.json'

        created_targets: list[Path] = []
        moved_pairs: list[tuple[Path, Path]] = []

        for source_path, target_path in self.R.items():
            try:
                if target_path.is_dir() or source_path.is_dir():
                    continue
                if target_path.exists():
                    if self.overwrite == '跳过':
                        # 跳过已存在目标，继续处理其他文件（不失败不回滚）
                        logger.info(f'[处理迁移] 目标文件已存在, 跳过: {target_path}')
                        self.skipped_mapping[source_path] = target_path
                        continue
                    logger.warning(f'[处理迁移] 目标文件已存在, 启用覆盖: {target_path}')
                    target_path.unlink()
                if not target_path.parent.exists():
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                if target_path.exists():
                    if self.overwrite == '跳过':
                        logger.info(f'[处理迁移] 目标文件在写入前再次出现, 跳过: {target_path}')
                        self.skipped_mapping[source_path] = target_path
                        continue
                    logger.warning(f'[处理迁移] 目标文件在写入前再次出现, 启用覆盖: {target_path}')
                    target_path.unlink()
                if self.mode == '剪切':
                    shutil.move(source_path, target_path)
                    moved_pairs.append((source_path, target_path))
                elif self.mode == '复制':
                    shutil.copy(source_path, target_path)
                    created_targets.append(target_path)
                elif self.mode == '链接':
                    try:
                        os.link(source_path, target_path)
                    except:  # noqa:E722
                        logger.warning('[处理迁移] 无法创建硬链接, 尝试软链接...')
                        os.symlink(source_path, target_path)
                    created_targets.append(target_path)
                else:
                    logger.error('[处理迁移] 模式错误！仅支持剪切, 复制, 链接')
                    self._rollback_transfers(created_targets, moved_pairs)
                    return 'partial_failure: invalid_mode'
                # 该文件本次实际落地（含覆盖重落），记入 landed_mapping 供统计/通知
                self.landed_mapping[source_path] = target_path
            except Exception as e:
                logger.error(str(e))
                try:
                    if self.mode in {'复制', '链接'} and target_path.exists():
                        target_path.unlink()
                    elif self.mode == '剪切' and target_path.exists():
                        if source_path.exists():
                            target_path.unlink()
                        else:
                            shutil.move(target_path, source_path)
                except Exception as cleanup_exc:  # noqa: BLE001
                    logger.warning(f'[处理迁移] 当前目标清理失败: {target_path} ({cleanup_exc})')
                self._rollback_transfers(created_targets, moved_pairs)
                return f'partial_failure: {e}'

        if self.write_record:
            # record 只写本次实际落地的 {源:目标}，不写跳过的——避免跳过模式下
            # record 含未落地条目，导致 transferred_file_count / TG「已入库 N 个文件」
            # 虚高（原始 R 含跳过项）。全跳过时 record 为空 dict，反映"本次 0 入库"。
            landed = {str(k): str(v) for k, v in self.landed_mapping.items()}
            with open(str(path), 'w', encoding='utf-8') as f:
                json.dump(landed, f, ensure_ascii=False)

        return True

    def _rollback_transfers(
        self,
        created_targets: list[Path],
        moved_pairs: list[tuple[Path, Path]],
    ) -> None:
        for target_path in reversed(created_targets):
            try:
                if target_path.exists() or target_path.is_symlink():
                    target_path.unlink()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f'[处理迁移] 回滚删除目标失败: {target_path} ({exc})')

        for source_path, target_path in reversed(moved_pairs):
            try:
                if target_path.exists():
                    shutil.move(target_path, source_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f'[处理迁移] 回滚移动失败，保留 partial_failure: {target_path} -> {source_path} ({exc})'
                )
