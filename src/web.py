from typing import cast
from pathlib import Path

from fastapi import Request
from nicegui import ui, app

from .models import TaskModel
from .main_page import main_page
from .rename.process import Rename
from .utils.utils import no_scroll_bar
from .pages.data_table_page import create_table


@ui.page('/')
def main():
    ui.add_head_html(no_scroll_bar)
    main_page()


@app.post('/sendTask')
async def _send_task(request: Request):
    data: TaskModel = cast(TaskModel, dict(await request.form()))
    path = data['path']
    is_anime = data['is_anime']
    no_process = data['no_process']

    if no_process:
        return {'code': 202, 'data': f'{path}忽略, 不处理！'}

    _path = Path(path)
    if not _path.exists():
        return {'code': 404, 'data': f'路径{path}不存在！'}

    Rename().process(_path, is_anime)
    create_table.refresh()
    return {'code': 200, 'data': '提交任务成功, 具体信息可以查看WebUI！'}
