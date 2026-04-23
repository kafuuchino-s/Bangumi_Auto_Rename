from __future__ import annotations

import os


os.environ.setdefault('BANGUMI_CONFIG_READONLY', '1')

from .runner import run_rename_regression

__all__ = ['run_rename_regression']
