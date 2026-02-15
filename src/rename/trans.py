import os
import json
import shutil
from typing import Dict, Optional
from pathlib import Path

from ..logger import logger
from ..utils.path import RECORD_PATH
from ..config.config_manager import cm


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
            force_overwrite: 强制覆盖选项，None则使用配置
            write_record: 是否写入 record 文件（data/record/{uuid}.json）
        """
        self.mode = force_mode or cm.get_config('mode')
        self.overwrite = (
            force_overwrite
            if force_overwrite is not None
            else cm.get_config('overwrite_existing')
        )
        self.R = R
        self.uuid = uuid
        self.write_record = write_record

    def trans_file(self):
        path = RECORD_PATH / f'{self.uuid}.json'

        _R = {str(k): str(v) for k, v in self.R.items()}

        if self.write_record:
            with open(str(path), 'w', encoding='utf-8') as f:
                json.dump(_R, f, ensure_ascii=False)

        for source_path, target_path in self.R.items():
            try:
                if target_path.is_dir() or source_path.is_dir():
                    continue
                if target_path.exists():
                    if self.overwrite:
                        logger.info(f'[处理迁移] 目标文件已存在, 覆盖: {target_path}')
                        target_path.unlink()
                    else:
                        logger.info(f'[处理迁移] 目标文件已存在, 跳过: {target_path}')
                        continue
                if not target_path.parent.exists():
                    target_path.parent.mkdir(parents=True)
                if self.mode == '剪切':
                    shutil.move(source_path, target_path)
                elif self.mode == '复制':
                    shutil.copy(source_path, target_path)
                elif self.mode == '链接':
                    try:
                        os.link(source_path, target_path)
                    except:  # noqa:E722
                        logger.warning('[处理迁移] 无法创建硬链接, 尝试软链接...')
                        os.symlink(source_path, target_path)
                else:
                    logger.error('[处理迁移] 模式错误！仅支持剪切, 复制, 链接')
            except Exception as e:
                logger.error(str(e))
                return str(e)
