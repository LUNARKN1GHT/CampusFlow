"""桌面模式的同源静态站点：托管构建后的 SPA，并为 history 路由回退 index.html。

只在 deployment_mode=desktop 且配置 static_dir 时安装。静态文件不做登录校验，
但绝不会接管 /api 路径；缺失的资源返回 404，不能回退成前端页面。
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from campusflow.core.config import Settings

logger = logging.getLogger(__name__)

# Vite 产物文件名带内容哈希，可长期缓存；index.html 每次校验，保证新版本立即生效。
_ASSET_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}
_NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}


def _is_api_path(full_path: str) -> bool:
    return full_path == "api" or full_path.startswith("api/")


def _is_asset_path(full_path: str) -> bool:
    return full_path == "assets" or full_path.startswith("assets/")


def _safe_file(root: Path, relative_path: str) -> Path | None:
    """只返回根目录内的真实文件；`..` 或符号链接越界一律视为不存在。"""
    candidate = (root / relative_path).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def install_static_spa(app: FastAPI, settings: Settings) -> None:
    if settings.deployment_mode != "desktop" or settings.static_dir is None:
        return
    root = Path(settings.static_dir).expanduser().resolve()
    index_path = root / "index.html"
    if not index_path.is_file():
        raise ValueError(f"桌面模式静态目录缺少 index.html：{root}")

    async def serve_spa(request: Request, exc: StarletteHTTPException) -> Response:
        # 只接管未匹配 GET/HEAD 页面请求；API 与方法不匹配等情况保持既有响应。
        full_path = request.url.path.lstrip("/")
        if (
            exc.status_code == 404
            and request.method in ("GET", "HEAD")
            and not _is_api_path(full_path)
        ):
            file_path = _safe_file(root, full_path)
            if file_path is not None:
                headers = _ASSET_CACHE_HEADERS if _is_asset_path(full_path) else _NO_CACHE_HEADERS
                return FileResponse(file_path, headers=headers)
            # 缺失的带扩展名资源或 assets 文件不回退 HTML，避免浏览器把页面当脚本解析。
            if _is_asset_path(full_path) or Path(full_path).suffix:
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return FileResponse(index_path, headers=_NO_CACHE_HEADERS)
        return await http_exception_handler(request, exc)

    app.add_exception_handler(StarletteHTTPException, serve_spa)
    logger.info("桌面模式静态站点已挂载：%s", root)
