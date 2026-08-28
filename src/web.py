from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException

from .config.config_manager import cm
from .logger import logger
from .queue.task_queue import get_queue_manager
from .api import api_router

# 纯 FastAPI 后端（已移除 NiceGUI）：提供 /api/* + /sendTask webhook + 前端静态托管
app = FastAPI(title="番剧自动重命名 API")


def _http_error_code(status_code: int, detail: object) -> tuple[str, dict[str, object]]:
    text = str(detail or "")
    lowered = text.lower()
    params: dict[str, object] = {}
    if status_code == 404:
        if "路径" in text or "path" in lowered:
            code = "path_not_found"
        elif "字幕" in text or "subtitle" in lowered:
            code = "subtitle_not_found"
        elif "任务" in text or "task" in lowered:
            code = "task_not_found"
        elif "文件" in text or "file" in lowered:
            code = "file_not_found"
        else:
            code = "resource_not_found"
    elif status_code == 409:
        code = "task_conflict"
    elif status_code == 403:
        code = "permission_denied"
    elif status_code == 422:
        code = "validation_error"
    elif status_code >= 500:
        code = "internal_error"
    else:
        code = "invalid_request"
    if code == "path_not_found" and text:
        params["path"] = text.split(":", 1)[-1].strip()
    return code, params


@app.exception_handler(HTTPException)
async def _api_http_error(request: Request, exc: HTTPException):
    if not request.url.path.startswith("/api"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    code, params = _http_error_code(exc.status_code, exc.detail)
    return JSONResponse(
        {"error": {"code": code, "params": params, "message": str(exc.detail or code)}},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def _api_validation_error(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api"):
        return JSONResponse({"detail": exc.errors()}, status_code=422)
    return JSONResponse(
        {
            "error": {
                "code": "validation_error",
                "params": {"fields": exc.errors()},
                "message": "Request validation failed",
            }
        },
        status_code=422,
    )

# 挂载 REST API 层（/api/*），供新前端调用；业务逻辑不动
app.include_router(api_router, prefix='/api')


@app.api_route('/api', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
@app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
async def _api_not_found(path: str = ''):
    """Keep unknown API paths inside the v2 error envelope."""
    target = f'/api/{path}' if path else '/api'
    raise HTTPException(status_code=404, detail=f'API path not found: {target}')


# 轻量健康检查端点：供 Docker HEALTHCHECK / 容器编排 / NAS GUI 探活。
# 必须在 _FRONTEND_OUT 的 StaticFiles mount('/') 之前注册，避免被前端 SPA 兜底吞掉。
# 不触发业务/队列，仅返回进程存活信号。
@app.get('/health')
def _health() -> dict[str, str]:
    return {'status': 'ok'}




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


def _parse_tag_list(value: object) -> list[str]:
    """将逗号分隔的配置或 qBittorrent 标签归一为精确匹配列表。"""
    if not isinstance(value, str):
        return []
    return [tag.strip().lower() for tag in value.split(",") if tag.strip()]


def _normalize_category(value: object) -> str:
    """归一 qBittorrent 单值分类；分类本身不按逗号拆分。"""
    return value.strip().lower() if isinstance(value, str) else ''


def get_skip_tags() -> list[str]:
    """从配置获取跳过标签列表。"""
    return _parse_tag_list(cm.get_config("skip_tags"))


def get_allowed_categories() -> list[str]:
    """从配置获取仅处理的分类白名单；空列表表示不限制。"""
    return _parse_tag_list(cm.get_config("allowed_categories"))


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
    # Windows 盘符大小写无关（H:\ 与 h:\ 指向同一卷），匹配前缀时按小写比较；
    # 但截断仍用原 host_prefix 长度，保留 path 后段原始大小写。
    if path.lower().startswith(host_prefix.lower()):
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
    is_anime_raw = form_data.get('is_anime')
    is_movie_raw = form_data.get('is_movie')
    is_anime = (
        _form_value_to_bool(is_anime_raw)
        if is_anime_raw is not None
        else None
    )
    is_movie = (
        _form_value_to_bool(is_movie_raw)
        if is_movie_raw is not None
        else None
    )
    no_process = _form_value_to_bool(form_data.get('no_process'))
    tag = _form_value_to_text(form_data.get('tag'))
    category = _normalize_category(form_data.get('category'))

    tag_list = _parse_tag_list(tag)

    if not path:
        logger.error('[结束任务] 路径为空！')
        return {'code': 400, 'data': '路径为空！'}

    # 转换宿主机路径到Docker路径
    path = convert_host_path_to_docker(path)

    # 修复 URL 编码导致的路径问题（+ 被解码为空格）
    path = fix_url_encoded_path(path)

    if no_process:
        logger.info(f'[结束任务] {path} 忽略：no_process 已启用。')
        return {'code': 202, 'data': f'{path}忽略, no_process 已启用！'}

    # 跳过标签优先于白名单，避免辅种等任务被意外放行。
    skip_tags = get_skip_tags()
    matched_skip_tag = next((tag for tag in tag_list if tag in skip_tags), None)
    if matched_skip_tag:
        logger.info(f'[结束任务] {path} 忽略：命中跳过标签 {matched_skip_tag}。')
        return {
            'code': 202,
            'data': f'{path}忽略, 命中跳过标签：{matched_skip_tag}！',
        }

    # 空白名单表示保持历史行为：所有 webhook 任务均可继续处理。
    allowed_categories = get_allowed_categories()
    if allowed_categories and category not in allowed_categories:
        received_category = category or '无'
        logger.info(
            f'[结束任务] {path} 忽略：未命中允许分类；'
            f'接收={received_category}，允许={",".join(allowed_categories)}。'
        )
        return {
            'code': 202,
            'data': (
                f'{path}忽略, 未命中允许分类！'
                f'接收：{received_category}；允许：{",".join(allowed_categories)}'
            ),
        }

    _path = Path(path)
    if not _path.exists():
        logger.error(f'[结束任务] 路径{path}不存在！')
        return {'code': 404, 'data': f'路径{path}不存在！'}

    # category 只用于 webhook 白名单准入，不参与媒体类型或落盘根判断。
    # 显式 is_anime/is_movie 仍保留给 API 调用方；qB 默认不传这两个字段。
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
# Vite 静态构建产物（frontend/out）由 FastAPI 同端口托管。
# /api/* 与 /sendTask 已注册路由优先；其余路径回退到前端 SPA。
_FRONTEND_OUT = Path(__file__).resolve().parents[1] / 'frontend' / 'out'


class SPAStaticFiles(StaticFiles):
    """Serve Vite assets and fall back only for HTML navigation requests.

    A normal ``StaticFiles(html=True)`` mount does not resolve a deep link such
    as ``/settings/general`` to the SPA entrypoint.  Conversely, falling back
    every 404 would hide missing JavaScript/CSS and misspelled API endpoints.
    This class keeps those boundaries explicit.
    """

    _RESERVED_PREFIXES = {'api', 'sendTask', 'health'}
    _MEDIA_TYPES = {
        '.css': 'text/css; charset=utf-8',
        '.js': 'text/javascript; charset=utf-8',
        '.mjs': 'text/javascript; charset=utf-8',
        '.json': 'application/json; charset=utf-8',
        '.svg': 'image/svg+xml',
        '.webmanifest': 'application/manifest+json',
    }

    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs['html'] = False
        super().__init__(*args, **kwargs)

    @classmethod
    def _is_reserved(cls, path: str) -> bool:
        first = path.lstrip('/').split('/', 1)[0]
        return first in cls._RESERVED_PREFIXES

    @classmethod
    def _set_media_type(cls, response: object, path: str) -> None:
        suffix = Path(path).suffix.casefold()
        media_type = cls._MEDIA_TYPES.get(suffix)
        if media_type and hasattr(response, 'headers'):
            response.headers['content-type'] = media_type  # type: ignore[attr-defined]

    async def get_response(self, path: str, scope: object):  # type: ignore[override]
        if self._is_reserved(path):
            raise HTTPException(status_code=404)
        if path in {'', '/', '.'}:
            response = await super().get_response('index.html', scope)  # type: ignore[arg-type]
            self._set_media_type(response, 'index.html')
            return response
        try:
            response = await super().get_response(path, scope)  # type: ignore[arg-type]
            self._set_media_type(response, path)
            return response
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            headers = Headers(scope=scope)  # type: ignore[arg-type]
            accepts_html = 'text/html' in headers.get('accept', '').lower()
            requested = Path(path)
            if (not accepts_html and path not in {'', '/'}) or requested.suffix:
                raise
            response = await super().get_response('index.html', scope)  # type: ignore[arg-type]
            self._set_media_type(response, 'index.html')
            return response


if _FRONTEND_OUT.is_dir():
    app.mount('/', SPAStaticFiles(directory=str(_FRONTEND_OUT)), name='frontend')
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
