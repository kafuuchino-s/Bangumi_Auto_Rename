from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config.config_manager import cm
from .logger import logger
from .queue.task_queue import get_queue_manager
from .api import api_router

# 纯 FastAPI 后端（已移除 NiceGUI）：提供 /api/* + /sendTask webhook + 前端静态托管
app = FastAPI(title="番剧自动重命名 API")

# 挂载 REST API 层（/api/*），供新前端调用；业务逻辑不动
app.include_router(api_router, prefix='/api')


# 轻量健康检查端点：供 Docker HEALTHCHECK / 容器编排 / NAS GUI 探活。
# 必须在 _FRONTEND_OUT 的 StaticFiles mount('/') 之前注册，避免被前端 SPA 兜底吞掉。
# 不触发业务/队列，仅返回进程存活信号。
@app.get('/health')
def _health() -> dict[str, str]:
    return {'status': 'ok'}


ANI_TAG = ['动漫', 'anime', '动画']
MOVIE_TAG = ['电影', 'movie', '剧场', '剧场版']


def _form_value_to_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ''
    return str(value)


def _form_value_to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return False


def get_skip_tags() -> list[str]:
    """从配置获取跳过标签列表"""
    skip_tags_raw = cm.get_config("skip_tags")
    skip_tags_str = skip_tags_raw if isinstance(skip_tags_raw, str) else ''
    return [tag.strip().lower() for tag in skip_tags_str.split(",") if tag.strip()]


def convert_host_path_to_docker(path: str) -> str:
    """
    将宿主机路径转换为Docker容器内路径

    例如: H:\\Anime\\xxx -> /media/Anime/xxx
    """
    host_prefix_raw = cm.get_config('host_path_prefix')
    docker_mnt_raw = cm.get_config('docker_mnt')
    host_prefix = host_prefix_raw if isinstance(host_prefix_raw, str) else ''
    docker_mnt = docker_mnt_raw if isinstance(docker_mnt_raw, str) else '/media'

    if not host_prefix:
        return path

    # 标准化宿主机前缀（确保结尾没有斜杠）
    host_prefix = host_prefix.rstrip('\\').rstrip('/')

    # 检查路径是否以宿主机前缀开头
    if path.startswith(host_prefix):
        # 移除宿主机前缀，替换为Docker挂载路径
        relative_path = path[len(host_prefix):]
        # 替换反斜杠为正斜杠
        relative_path = relative_path.replace('\\', '/')
        # 确保docker_mnt结尾没有斜杠
        docker_mnt = docker_mnt.rstrip('/')
        new_path = docker_mnt + relative_path
        logger.info(f'[路径转换] {path} -> {new_path}')
        return new_path

    return path


def fix_url_encoded_path(path: str) -> str:
    """
    修复 URL 编码导致的路径问题

    在 application/x-www-form-urlencoded 中，特殊字符可能被错误解码：
    - + 被解码为空格
    - & 可能导致截断
    - 其他特殊字符问题

    此函数尝试在父目录中找到最匹配的文件/文件夹
    """
    import re

    _path = Path(path)
    if _path.exists():
        return path

    # 获取父目录和目标名称
    parent = _path.parent
    target_name = _path.name

    if not parent.exists():
        return path

    # 将目标名称中的连续空格标准化为单个空格，用于比较
    def normalize(s: str) -> str:
        return re.sub(r'\s+', ' ', s).strip().lower()

    target_normalized = normalize(target_name)

    # 在父目录中查找最匹配的项
    best_match = None
    best_score = 0

    try:
        for item in parent.iterdir():
            item_normalized = normalize(item.name)

            # 完全匹配（标准化后）
            if item_normalized == target_normalized:
                best_match = item
                break

            # 计算相似度：去掉所有非字母数字字符后比较
            def alphanum_only(s: str) -> str:
                return re.sub(r'[^a-z0-9]', '', s.lower())

            target_alphanum = alphanum_only(target_name)
            item_alphanum = alphanum_only(item.name)

            if target_alphanum == item_alphanum:
                # 字母数字部分完全匹配，很可能是同一个
                score = len(target_alphanum)
                if score > best_score:
                    best_score = score
                    best_match = item

    except PermissionError:
        return path

    if best_match and best_match.name != target_name:
        fixed_path = str(best_match)
        logger.info(f'[路径修复] {path} -> {fixed_path}')
        return fixed_path

    return path


@app.post('/sendTask')
async def _send_task(request: Request):
    form_data = dict(await request.form())
    logger.info(f'[收到任务] {form_data}')
    raw_path = _form_value_to_text(form_data.get('path'))

    # qBittorrent 有时会以 UTF-8 bytes 发送，但 ASGI 表单解析可能按 latin-1 解码成 str。
    # 对“已是正常 Unicode 的路径”不要强行 latin-1 编码，否则遇到中文/日文会直接崩溃。
    path = raw_path
    try:
        recovered = raw_path.encode('latin1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        recovered = None
    if recovered and recovered != raw_path:
        path = recovered
    is_anime: bool | None = _form_value_to_bool(form_data.get('is_anime'))
    no_process = _form_value_to_bool(form_data.get('no_process'))
    tag = _form_value_to_text(form_data.get('tag'))

    tag_list = [str(i).strip().lower() for i in tag.split(',')]

    # 检查是否包含跳过标签（IYUU辅种等）
    skip_tags = get_skip_tags()
    for skip in skip_tags:
        if skip in tag_list:
            no_process = True
            logger.info(f'[收到任务] 检测到跳过标签: {skip}')
            break

    if not path:
        logger.error('[结束任务] 路径为空！')
        return {'code': 400, 'data': '路径为空！'}

    # 转换宿主机路径到Docker路径
    path = convert_host_path_to_docker(path)

    # 修复 URL 编码导致的路径问题（+ 被解码为空格）
    path = fix_url_encoded_path(path)

    if no_process:
        logger.info(f'[结束任务] {path}忽略, 不处理！')
        return {'code': 202, 'data': f'{path}忽略, 不处理！'}

    _path = Path(path)
    if not _path.exists():
        logger.error(f'[结束任务] 路径{path}不存在！')
        return {'code': 404, 'data': f'路径{path}不存在！'}

    if not is_anime:
        for i in ANI_TAG:
            if i in tag_list:
                is_anime = True
                break
        else:
            # 没有动漫标签时设为 None，让后续代码根据 TMDB genre 自动判断
            is_anime = None
    else:
        is_anime = None

    for i in MOVIE_TAG:
        if i in tag_list:
            is_movie = True
            break
    else:
        is_movie = None

    queue_mgr = get_queue_manager()

    # 检查是否已在队列中
    if queue_mgr.is_path_in_queue(str(_path)):
        logger.info(f'[收到任务] {path} 已在队列中')
        return {'code': 200, 'data': '任务已在队列中！'}

    # 加入队列
    task_id = queue_mgr.enqueue(
        path=str(_path),
        is_anime=is_anime,
        is_movie=is_movie,
    )

    logger.info(f'[收到任务] {path} 已加入队列，任务ID: {task_id}')
    return {'code': 200, 'data': '任务已加入队列！'}


# ----------------------- 前端静态托管（单端口合一）----------------------- #
# Next.js 静态导出产物（frontend/out）由 FastAPI 同端口托管。
# /api/* 与 /sendTask 已注册路由优先；其余路径回退到前端 SPA。
_FRONTEND_OUT = Path(__file__).resolve().parents[1] / 'frontend' / 'out'
if _FRONTEND_OUT.is_dir():
    app.mount('/', StaticFiles(directory=str(_FRONTEND_OUT), html=True), name='frontend')
else:
    # 前端未构建：给出提示，避免 StaticFiles 报错
    @app.get('/', response_class=HTMLResponse)
    def _frontend_not_built():
        return (
            '<html><body style="font-family:system-ui;padding:40px">'
            '<h2>番剧自动重命名 · API 后端</h2>'
            '<p>前端未构建。请在 <code>frontend/</code> 执行 <code>npm run build</code> '
            '生成静态产物后重启。</p>'
            '<p>API：<code>/api/*</code> ｜ webhook：<code>POST /sendTask</code></p>'
            '</body></html>'
        )
