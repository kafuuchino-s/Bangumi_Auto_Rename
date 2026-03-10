import asyncio
from pathlib import Path
from typing import Any, Dict, List

from nicegui import ui
from nicegui.events import GenericEventArguments

from ..element.red import notify, RedButton
from ..logger import logger
from ..notification.emby_notify import get_emby_notifier
from ..queue.task_queue import get_queue_manager
from ..queue.task_status import TaskStatus
from ..utils.path import RECORD_PATH, TASK_PATH
from ..utils.utils import get_task
from .edit_page import edit_page


@ui.refreshable
def create_subtitle_table() -> None:
    """创建字幕任务表格"""
    subtitle_rows = []
    subtitle_columns: List[Dict[str, Any]] = [
        {'name': 'id', 'label': 'ID', 'field': 'id'},
        {'name': 'value', 'label': '操作', 'field': 'value'},
        {'name': 'archive', 'label': '压缩包', 'field': 'archive'},
        {'name': 'matched_task', 'label': '匹配动漫', 'field': 'matched_task'},
        {'name': 'matched_count', 'label': '匹配数', 'field': 'matched_count'},
        {'name': 'sync', 'label': '对齐', 'field': 'sync'},
        {'name': 'status', 'label': '状态', 'field': 'status'},
        {'name': 'uuid', 'label': 'UUID', 'field': 'uuid'},
    ]
    for col in subtitle_columns:
        col['align'] = 'center'
        col['sortable'] = True

    if not TASK_PATH.exists():
        ui.label('暂无字幕导入记录').style('color: #666;')
        return

    sorted_files = sorted(
        TASK_PATH.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True
    )
    for index, i in enumerate(sorted_files):
        task_data = get_task(i.stem)

        # 只处理字幕任务
        if task_data.get('type') != 'subtitle':
            continue

        status = '成功' if task_data.get('status') == 'success' else '失败'
        archive_name = Path(task_data.get('archive_path', '')).name

        sync_summary = task_data.get('sync_summary') or {}
        if sync_summary.get('enabled'):
            attempted = sync_summary.get('attempted', 0)
            success = sync_summary.get('success', 0)
            fallback = sync_summary.get('fallback', 0)
            sync_text = f"{success}/{attempted}, 回退{fallback}"
        else:
            sync_text = '-'

        subtitle_rows.append({
            'id': index,
            'archive': archive_name,
            'archive_path': task_data.get('archive_path', ''),
            'matched_task': task_data.get('matched_task', '-'),
            'matched_count': f"{task_data.get('matched_count', 0)}/{task_data.get('total_subtitles', 0)}",
            'sync': sync_text,
            'status': status,
            'uuid': task_data.get('uuid', ''),
            'value': '操作',
        })

    if not subtitle_rows:
        ui.label('暂无字幕导入记录').style('color: #666;')
        return

    # 清除全部文件按钮
    with ui.row().classes('w-full justify-end mb-2'):
        RedButton(
            '🗑️ 清除全部压缩包',
            on_click=lambda: _handle_clean_all_archives(subtitle_rows),
        ).props('outline size=sm')

    table = (
        ui.table(columns=subtitle_columns, rows=subtitle_rows)
        .classes('w-full h-full rounded')
        .style('max-height: 90%; border-radius: 10px;')
    )
    table._props['visible-columns'] = [
        'id', 'archive', 'matched_task', 'matched_count', 'sync', 'status', 'value',
    ]

    table.add_slot(
        'body-cell-value',
        """
        <q-td :props="props">
            <q-btn @click="$parent.$emit('retry_subtitle', props)" label="重试" color='green-6' class="q-mr-sm rounded" style="border-radius: 5rem"/>
            <q-btn @click="$parent.$emit('del_subtitle', props)" label="删除" color='red-6' class="q-mr-sm rounded" style="border-radius: 5rem"/>
            <q-btn @click="$parent.$emit('clean_archive', props)" label="清除文件" color='grey-6' class="q-mr-sm rounded" style="border-radius: 5rem"/>
        </q-td>
    """,
    )

    table.add_slot(
        'body-cell-id',
        '''
        <q-td
            :props="props"
            :class="{
            'bg-green-4 text-white': props.row.status === '成功',
            'bg-red-4 text-white': props.row.status !== '成功'
            }"
        >
        {{props.value}}
        </q-td>''',
    )

    table.on('del_subtitle', lambda ev: _handle_subtitle_delete(ev))
    table.on(
        'retry_subtitle',
        lambda ev: asyncio.create_task(
            _handle_subtitle_retry(ev, ui.context.client)
        ),
    )
    table.on('clean_archive', lambda ev: _handle_clean_archive(ev))


def _handle_subtitle_delete(ev: GenericEventArguments) -> None:
    """删除字幕任务记录"""
    arg = ev.args
    row_data = arg['row']
    uuid = row_data['uuid']

    path1 = TASK_PATH / f'{uuid}.json'
    if path1.exists():
        path1.unlink()

    notify(f'删除字幕任务记录成功!')
    create_subtitle_table.refresh()


def _handle_clean_archive(ev: GenericEventArguments) -> None:
    """清除压缩包文件"""
    arg = ev.args
    row_data = arg['row']
    archive_path = Path(row_data['archive_path'])

    if not archive_path.exists():
        notify('压缩包已不存在')
        return

    try:
        archive_path.unlink()
        notify(f'已清除压缩包: {archive_path.name}')
    except Exception as e:
        notify(f'清除失败: {e}')


def _handle_clean_all_archives(subtitle_rows: List[Dict[str, Any]]) -> None:
    """清除全部压缩包文件"""
    cleaned = 0
    failed = 0

    for row in subtitle_rows:
        archive_path = Path(row['archive_path'])
        if archive_path.exists():
            try:
                archive_path.unlink()
                cleaned += 1
            except Exception:
                failed += 1

    if failed > 0:
        notify(f'已清除 {cleaned} 个压缩包，{failed} 个失败')
    elif cleaned > 0:
        notify(f'已清除全部 {cleaned} 个压缩包')
    else:
        notify('没有需要清除的压缩包')


async def _handle_subtitle_retry(ev: GenericEventArguments, client) -> None:
    """重试字幕任务"""
    from nicegui import run
    from ..subtitle.processor import SubtitleProcessor

    arg = ev.args
    row_data = arg['row']
    archive_path = Path(row_data['archive_path'])
    old_uuid = row_data['uuid']

    with client.content:
        # 检查压缩包是否存在
        if not archive_path.exists():
            notify(f'压缩包不存在: {archive_path.name}')
            return

        # 删除旧记录
        old_task_path = TASK_PATH / f'{old_uuid}.json'
        if old_task_path.exists():
            old_task_path.unlink()

        notify('正在重新处理字幕...')

        # 重新处理
        processor = SubtitleProcessor()
        result = await run.io_bound(processor.process, archive_path)

        if result['status'] == 'success':
            notify(f"重试成功! 匹配 {result['matched_count']} 个字幕文件")

            # 字幕有变更时，通知 Emby 刷新媒体库
            try:
                emby = get_emby_notifier()
                if emby.is_available():
                    success, message = emby.refresh_library()
                    if success:
                        notify(f"已通知 Emby 刷新媒体库: {message}", type="positive")
                    else:
                        notify(f"Emby 刷新失败: {message}", type="warning")
                else:
                    logger.info("[字幕导入] Emby 通知未启用或未配置，跳过刷新")
            except Exception as e:
                logger.error(f"[字幕导入] Emby 通知异常: {e}")
                notify(f"Emby 通知异常: {e}", type="warning")
        else:
            notify(f"重试失败: {result.get('error', '未知错误')}")

        create_subtitle_table.refresh()


def _format_bool_text(value: Any) -> str:
    if value is True:
        return '是'
    if value is False:
        return '否'
    return '自动'


@ui.refreshable
def create_table():
    queue_mgr = get_queue_manager()

    rows = []
    columns: List[Dict[str, Any]] = [
        {'name': 'id', 'label': 'ID', 'field': 'id'},
        {'name': 'value', 'label': '操作', 'field': 'value'},
        {'name': 'path', 'label': '传入路径', 'field': 'path'},
        {'name': 'name', 'label': '识别剧集', 'field': 'name'},
        {'name': 'season', 'label': '季度', 'field': 'season'},
        {'name': 'status', 'label': '状态', 'field': 'status'},
        {'name': 'queue_status', 'label': '队列状态', 'field': 'queue_status'},
        {'name': 'uuid', 'label': 'UUID', 'field': 'uuid'},
        {'name': 'is_anime', 'label': '是否为动漫', 'field': 'is_anime'},
        {'name': 'is_movie', 'label': '是否为电影', 'field': 'is_movie'},
        {'name': 'ai_used', 'label': 'AI识别', 'field': 'ai_used'},
    ]
    for j in columns:
        j['align'] = 'center'
        j['sortable'] = True

    task_rows_by_path: Dict[str, Dict[str, Any]] = {}
    if TASK_PATH.exists():
        sorted_files = sorted(
            TASK_PATH.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True
        )
    else:
        sorted_files = []
    for i in sorted_files:
        task_data = get_task(i.stem)

        # 跳过字幕任务（单独处理）
        if task_data.get('type') == 'subtitle':
            continue

        path = task_data.get('path', '')
        if not path:
            continue

        if task_data.get('error'):
            status = task_data['error']
        else:
            status = '成功'

        ai_used = task_data.get('ai_used', task_data.get('use_ai', False))
        ai_status = '是' if ai_used else '否'

        queue_status = queue_mgr.get_path_status(path)
        if queue_status == TaskStatus.RUNNING:
            queue_status_text = '执行中...'
        elif queue_status == TaskStatus.PENDING:
            position = queue_mgr.get_queue_position(path)
            queue_status_text = f'队列中 #{position}'
        else:
            queue_status_text = '-'

        task_rows_by_path[path] = {
            'path': path,
            'name': task_data.get('name', '未知'),
            'uuid': task_data.get('uuid', ''),
            'season': task_data.get('season_id', '-'),
            'status': status,
            'queue_status': queue_status_text,
            'is_anime': task_data.get('is_anime', False),
            'is_movie': task_data.get('is_movie', False),
            'ai_used': ai_status,
            'value': '操作',
        }

    for task in queue_mgr.list_active_tasks():
        if task.path in task_rows_by_path:
            continue

        if task.status == TaskStatus.RUNNING:
            queue_status_text = '执行中...'
            status = '处理中'
        else:
            position = queue_mgr.get_queue_position(task.path)
            queue_status_text = f'队列中 #{position}'
            status = '等待处理'

        task_rows_by_path[task.path] = {
            'path': task.path,
            'name': task.cus_name or Path(task.path).name,
            'uuid': task.original_uuid or task.task_id,
            'season': task.cus_season_id or '-',
            'status': status,
            'queue_status': queue_status_text,
            'is_anime': _format_bool_text(task.is_anime),
            'is_movie': _format_bool_text(task.is_movie),
            'ai_used': '待处理',
            'value': '操作',
        }

    for index, row in enumerate(task_rows_by_path.values()):
        row['id'] = index
        rows.append(row)

    table = (
        ui.table(columns=columns, rows=rows)
        .classes('w-full h-full rounded')
        .style('max-height: 90%; border-radius: 10px;separator: cell')
    )
    table._props['visible-columns'] = [
        'id',
        'path',
        'name',
        'season',
        'status',
        'queue_status',
        'ai_used',
        'value',
    ]

    table.add_slot(
        'body-cell-value',
        """
        <q-td :props="props">
            <q-btn @click="$parent.$emit('retry', props)" label="重试" color='green-6' class="q-mr-sm rounded" style="border-radius: 5rem"/>
            <q-btn @click="$parent.$emit('edit', props)" label="编辑" color='blue-6' class="q-mr-sm rounded" style="border-radius: 5rem"/>
            <q-btn @click="$parent.$emit('del', props)" label="删除" color='red-6' class="q-mr-sm rounded" style="border-radius: 5rem"/>
        </q-td>
    """,  # noqa: E501
    )

    table.add_slot(
        'body-cell-id',
        '''
        <q-td
            :props="props"
            :class="{
            'bg-green-4 text-white': props.row.status === '成功',
            'bg-red-4 text-white': props.row.status !== '成功'
            }"
        >
        {{props.value}}
        </q-td>''',  # noqa: E501
    )

    # 队列状态列样式
    table.add_slot(
        'body-cell-queue_status',
        '''
        <q-td
            :props="props"
            :class="{
            'bg-blue-2': props.value.startsWith('执行中'),
            'bg-orange-2': props.value.startsWith('队列中')
            }"
        >
            <q-badge
                v-if="props.value !== '-'"
                :color="props.value.startsWith('执行中') ? 'blue' : 'orange'"
            >
                {{ props.value }}
            </q-badge>
            <span v-else>-</span>
        </q-td>''',  # noqa: E501
    )

    table.on('action', lambda msg: print(msg))
    table.on(
        'retry',
        lambda ev, c=ui.context.client: asyncio.create_task(handle_retry(ev, c))
    )
    table.on(
        'edit',
        lambda ev, c=ui.context.client: asyncio.create_task(handle_edit(ev, c))
    )
    table.on('del', lambda ev: handle_delete(ev))



async def handle_edit(ev: GenericEventArguments, client):
    arg = ev.args
    uuid = arg['row']['uuid']
    with client.content:
        await edit_page(uuid)
    create_table.refresh()


async def handle_retry(ev: GenericEventArguments, client):
    """处理重试按钮点击 - 将任务加入队列"""
    arg = ev.args
    row_data = arg['row']
    path = row_data['path']
    is_anime = row_data['is_anime']
    is_movie = row_data['is_movie']
    uuid_str = row_data['uuid']

    queue_mgr = get_queue_manager()

    with client.content:
        # 检查是否已在队列中
        if queue_mgr.is_path_in_queue(path):
            notify('该任务已在队列中！')
            return

        # 删除旧任务记录
        handle_delete(ev, is_notify=False)

        # 加入队列（队列会自动通知UI刷新）
        queue_mgr.enqueue(
            path=path,
            is_anime=is_anime,
            is_movie=is_movie,
            original_uuid=uuid_str,
        )

        notify('任务已加入队列！')


def handle_delete(ev: GenericEventArguments, is_notify: bool = True):
    arg = ev.args
    row_data = arg['row']
    uuid = row_data['uuid']

    path1 = TASK_PATH / f'{uuid}.json'
    path2 = RECORD_PATH / f'{uuid}.json'

    if path1.exists():
        path1.unlink()
    if path2.exists():
        path2.unlink()

    if is_notify:
        notify(f'删除任务记录{uuid}成功!')

    create_table.refresh()
