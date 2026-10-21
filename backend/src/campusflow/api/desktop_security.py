"""桌面模式的本机请求防护：Host 允许列表、跨站写入拦截与安全响应头。

桌面 sidecar 只监听回环地址，但浏览器仍可能把恶意页面指向 `127.0.0.1`（DNS
重绑定）或伪造跨站写入（CSRF）。本中间件只在 `deployment_mode=desktop` 时安装：

- 解析后的 Host 必须是 `127.0.0.1`、`localhost` 或 `::1`（可带端口），否则直接
  返回 400 JSON，不进入路由；`X-Forwarded-*` 一律不参与判断。
- POST/PUT/PATCH/DELETE 若带 `Origin`，必须是本机 HTTP 回环源，否则返回
  403 JSON；不带 `Origin` 的非浏览器客户端放行。
- 所有响应附加 `nosniff`、`no-referrer` 与 `X-Frame-Options: DENY`；仅 HTML
  响应附加严格 CSP。`/docs`、`/redoc` 依赖 CDN 资源，显式跳过 CSP。

使用纯 ASGI 中间件，只在 `http.response.start` 上补头部，不读取请求体，
因此不会缓冲上传内容。服务端模式完全不安装，行为保持不变。
"""

import json
from typing import Final
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from campusflow.core.config import Settings

_ALLOWED_HOSTNAMES: Final = frozenset({"127.0.0.1", "localhost", "::1"})
_STATE_CHANGING_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Swagger UI / ReDoc 从 CDN 加载脚本与样式，套用严格 CSP 会让文档页白屏。
_CSP_EXEMPT_PATHS: Final = ("/docs", "/redoc", "/openapi.json")

_BASE_HEADERS: Final = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
)

CONTENT_SECURITY_POLICY: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)

HOST_REJECTED_MESSAGE: Final = (
    "桌面模式仅接受本机回环地址访问，请通过 127.0.0.1 或 localhost 打开。"
)
ORIGIN_REJECTED_MESSAGE: Final = "桌面模式拒绝跨站请求：请求来源必须与本机页面同源。"


def _header_values(scope: Scope, name: bytes) -> list[str]:
    return [
        value.decode("latin-1")
        for key, value in scope.get("headers", [])
        if key.lower() == name
    ]


def _valid_port(port: str) -> bool:
    return port.isdigit() and 1 <= int(port) <= 65535


def split_host_header(value: str) -> tuple[str, str] | None:
    """拆分 Host 头为 (主机名, 端口)；格式不合法时返回 None。

    只接受回环字面量。IPv6 只支持 `[::1]`／`[::1]:port`，同时兼容某些测试客户端
    直接发送的裸 `::1`（HTTP 规范要求带方括号，故其它裸 IPv6 一律视为畸形）。
    """
    if not value or value != value.strip():
        return None
    # 用户信息、路径与空白不可能出现在合法 Host 中；出现即判畸形而不是猜测。
    if any(character in value for character in "/\\@?#,; \t\r\n"):
        return None

    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            return None
        hostname = value[1:closing]
        remainder = value[closing + 1 :]
        if not hostname or any(character in hostname for character in "[]"):
            return None
        if remainder:
            if not remainder.startswith(":") or not _valid_port(remainder[1:]):
                return None
            return hostname, remainder[1:]
        return hostname, ""

    if value.count(":") > 1:
        return ("::1", "") if value.lower() == "::1" else None

    hostname, separator, port = value.partition(":")
    if not hostname:
        return None
    if separator and not _valid_port(port):
        return None
    return hostname, port


def is_allowed_host(host_header: str) -> bool:
    parsed = split_host_header(host_header)
    return parsed is not None and parsed[0].lower() in _ALLOWED_HOSTNAMES


def is_allowed_origin(origin: str) -> bool:
    """只接受 `http://` 回环源；Origin 不含路径，出现即视为伪造。"""
    if not origin or origin != origin.strip():
        return False
    parts = urlsplit(origin)
    if parts.scheme != "http" or parts.path or parts.query or parts.fragment:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    try:
        port = parts.port
    except ValueError:  # 端口不是数字或越界
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    hostname = parts.hostname
    return hostname is not None and hostname.lower() in _ALLOWED_HOSTNAMES


def _csp_applies(path: str) -> bool:
    return not any(
        path == exempt or path.startswith(exempt + "/") for exempt in _CSP_EXEMPT_PATHS
    )


async def _reject(send: Send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
        *_BASE_HEADERS,
    ]
    await send({"type": "http.response.start", "status": status_code, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class DesktopSecurityMiddleware:
    """仅桌面模式安装；请求进入路由前校验 Host 与写入请求的 Origin。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        hosts = _header_values(scope, b"host")
        # 缺失或多个 Host 都属于畸形请求；绝不能用替代来源补猜。
        if len(hosts) != 1 or not is_allowed_host(hosts[0]):
            await _reject(send, 400, HOST_REJECTED_MESSAGE)
            return

        if scope["method"].upper() in _STATE_CHANGING_METHODS:
            origins = _header_values(scope, b"origin")
            # 没有 Origin 的请求来自非浏览器客户端；不能据此推断为恶意。
            if origins and (len(origins) != 1 or not is_allowed_origin(origins[0])):
                await _reject(send, 403, ORIGIN_REJECTED_MESSAGE)
                return

        path = scope.get("path", "")
        html_csp = _csp_applies(path)

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _BASE_HEADERS:
                    headers.setdefault(name.decode("ascii"), value.decode("ascii"))
                content_type = headers.get("content-type", "")
                if html_csp and content_type.startswith("text/html"):
                    headers.setdefault("content-security-policy", CONTENT_SECURITY_POLICY)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


def install_desktop_security(app: FastAPI, settings: Settings) -> None:
    """桌面模式安装防护中间件；服务端模式无操作。"""
    if settings.deployment_mode != "desktop":
        return
    app.add_middleware(DesktopSecurityMiddleware)
