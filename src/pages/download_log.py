from nicegui import ui

from ..utils.path import log_path


def download_log():
    ui.download(log_path, log_path.name)
