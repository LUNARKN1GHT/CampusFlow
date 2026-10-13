"""N004：只使用标准库、固定已检查 IP 的有界网页抓取。"""

from __future__ import annotations

import http.client
import ipaddress
import math
import socket
import ssl
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from campusflow.application.ports.web_fetch import FetchedWebPage, WebFetchError

# DNS 调用不能被 Python 强制取消。限制仍在运行的线程数量，不无限堆积解析任务。
_SLOTS = threading.BoundedSemaphore(4)
_REDIRECTS = {301, 302, 303, 307, 308}
_IPV6_PUBLIC = ipaddress.ip_network("2000::/3")
_SPECIAL_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("192.0.0.0/24", "2001::/23", "2002::/16")
)


@dataclass(frozen=True)
class FetchLimits:
    max_bytes: int = 2 * 1024 * 1024
    max_redirects: int = 3
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if (
            type(self.max_bytes) is not int
            or self.max_bytes < 1
            or type(self.max_redirects) is not int
            or not 0 <= self.max_redirects <= 10
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("抓取大小和超时必须为正数，跳转上限为 0 至 10 次")


@dataclass(frozen=True)
class _Target:
    url: str
    scheme: str
    host: str
    port: int
    path: str


def _target(url: str) -> _Target:
    if (
        not isinstance(url, str)
        or not url
        or len(url) > 8192
        or "\\" in url
        or any(ord(char) <= 32 or ord(char) == 127 for char in url)
    ):
        raise WebFetchError("invalid_url", "请输入完整的公开网页网址，不要包含空白或控制字符")
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "%" in parts.hostname
        ):
            raise ValueError
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if port != (443 if parts.scheme == "https" else 80):
            raise ValueError
        if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise WebFetchError("blocked_address", "不能读取本机或内网网址")
        authority = f"[{host}]" if ":" in host else host
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
        canonical = urlunsplit((parts.scheme, authority, path, query, ""))
        return _Target(canonical, parts.scheme, host, port, path + (f"?{query}" if query else ""))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, WebFetchError):
            raise
        raise WebFetchError(
            "invalid_url", "仅支持不带登录信息的 HTTP/HTTPS 网页及其默认端口"
        ) from None


def _check_address(address: str) -> None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        raise WebFetchError("blocked_address", "网址未解析到可验证的公网地址") from None
    if (
        not ip.is_global
        or ip.is_multicast
        or ip.is_unspecified
        or (ip.version == 6 and ip not in _IPV6_PUBLIC)
        or any(ip.version == network.version and ip in network for network in _SPECIAL_NETWORKS)
        or str(ip) == "168.63.129.16"  # Azure 平台服务地址，不是公开网页源。
    ):
        raise WebFetchError("blocked_address", "不能读取本机、内网或保留地址")


class _Request:
    def __init__(self, timeout: float):
        self.deadline = time.monotonic() + timeout
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.sock: socket.socket | None = None

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise WebFetchError("timeout", "网页读取超时，请稍后重试或粘贴原文")
        return remaining

    def track(self, sock: socket.socket) -> None:
        with self.lock:
            if self.cancelled.is_set():
                sock.close()
                raise WebFetchError("timeout", "网页读取超时，请稍后重试或粘贴原文")
            self.sock = sock

    def abort(self) -> None:
        with self.lock:
            self.cancelled.set()
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, target: _Target, address: tuple, request: _Request):
        super().__init__(target.host, target.port, timeout=request.remaining())
        self.target = target
        self.address = address
        self.request_budget = request

    def connect(self) -> None:
        family, socktype, proto, _, sockaddr = self.address
        budget = self.request_budget
        sock = socket.socket(family, socktype, proto)
        self.sock = sock
        budget.track(sock)
        sock.settimeout(budget.remaining())
        # sockaddr 来自已验证的 DNS 结果；这里绝不能再用域名调用 create_connection。
        sock.connect(sockaddr)
        if self.target.scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(
                sock, server_hostname=self.target.host, do_handshake_on_connect=False
            )
            self.sock = sock
            budget.track(sock)
            sock.settimeout(budget.remaining())
            sock.do_handshake()


def _resolve(target: _Target, request: _Request) -> list[tuple]:
    # 字面 IP 也先检查，避免将明显内网地址交给解析器。
    try:
        ipaddress.ip_address(target.host)
    except ValueError:
        pass
    else:
        _check_address(target.host)
    request.remaining()
    addresses = socket.getaddrinfo(target.host, target.port, type=socket.SOCK_STREAM)
    request.remaining()
    if not addresses:
        raise WebFetchError("unreachable", "网页地址无法解析，请检查网址或粘贴原文")
    for family, socktype, _, _, sockaddr in addresses:
        if family not in {socket.AF_INET, socket.AF_INET6} or socktype != socket.SOCK_STREAM:
            raise WebFetchError("blocked_address", "网址未解析到可验证的公网地址")
        _check_address(sockaddr[0])
        if family == socket.AF_INET6 and sockaddr[3] != 0:
            raise WebFetchError("blocked_address", "不能读取带本地区域标识的地址")
    return addresses


class PublicWebFetcher:
    """显式调用才联网；无代理、Cookie、凭据、自动爬取或自动重试。"""

    def __init__(self, limits: FetchLimits | None = None):
        self.limits = limits or FetchLimits()

    def fetch(self, requested_url: str) -> FetchedWebPage:
        target = _target(requested_url)
        if not _SLOTS.acquire(blocking=False):
            raise WebFetchError("busy", "网页读取繁忙，请稍后重试")
        request = _Request(self.limits.timeout_seconds)
        result: Future[FetchedWebPage] = Future()

        def run() -> None:
            try:
                result.set_result(self._fetch(target, request))
            except WebFetchError as exc:
                result.set_exception(exc)
            except TimeoutError:
                result.set_exception(WebFetchError("timeout", "网页读取超时，请稍后重试或粘贴原文"))
            except (OSError, http.client.HTTPException, ValueError):
                result.set_exception(WebFetchError("unreachable", "网页无法读取，请重试或粘贴原文"))
            except Exception as exc:
                result.set_exception(exc)
            finally:
                request.abort()
                _SLOTS.release()

        worker = threading.Thread(target=run, name="public-web-fetch", daemon=True)
        try:
            worker.start()
        except RuntimeError:
            _SLOTS.release()
            raise
        try:
            return result.result(timeout=max(0, request.deadline - time.monotonic()))
        except TimeoutError:
            raise WebFetchError("timeout", "网页读取超时，请稍后重试或粘贴原文") from None
        finally:
            request.abort()

    def _fetch(self, initial: _Target, request: _Request) -> FetchedWebPage:
        target = initial
        visited: set[str] = set()
        for hop in range(self.limits.max_redirects + 1):
            request.remaining()
            if target.url in visited:
                raise WebFetchError("redirect_limit", "网页跳转循环，请使用最终公开网址")
            visited.add(target.url)
            addresses = _resolve(target, request)
            connection = _PinnedConnection(target, addresses[0], request)
            try:
                connection.request(
                    "GET",
                    target.path,
                    headers={
                        "User-Agent": "CampusFlow/0.1 (user-requested import)",
                        "Accept": "text/html, text/plain, application/xhtml+xml",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                if response.status in _REDIRECTS:
                    locations = response.headers.get_all("Location", [])
                    if len(locations) != 1 or not locations[0]:
                        raise WebFetchError("invalid_redirect", "网页跳转目标缺失或不明确")
                    if hop == self.limits.max_redirects:
                        raise WebFetchError("redirect_limit", "网页跳转过多，请使用最终公开网址")
                    # 先拒绝原始 Location 中的控制字符，不能让 urljoin 静默清理。
                    location = locations[0]
                    if any(ord(char) <= 32 or ord(char) == 127 for char in location):
                        raise WebFetchError("invalid_redirect", "网页跳转地址无效")
                    target = _target(urljoin(target.url, location))
                    continue
                if response.status in {401, 403}:
                    raise WebFetchError(
                        "access_denied", "网页需要登录或拒绝访问，请上传文件或粘贴原文"
                    )
                if response.status != 200:
                    raise WebFetchError("http_status", "网页未正常返回内容，请检查网址或粘贴原文")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise WebFetchError("unsupported_encoding", "暂不支持压缩网页响应，请粘贴原文")
                media_type = response.headers.get_content_type()
                if media_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                    raise WebFetchError(
                        "unsupported_type", "网址返回的不是网页或文本，请改用文件上传"
                    )
                lengths = response.headers.get_all("Content-Length", [])
                if lengths:
                    if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                        raise WebFetchError("invalid_response", "网页响应长度无效")
                    if int(lengths[0]) > self.limits.max_bytes:
                        raise WebFetchError(
                            "too_large", "网页超过允许大小，请上传文件或粘贴所需原文"
                        )
                body = bytearray()
                while True:
                    request.remaining()
                    chunk = response.read1(min(65536, self.limits.max_bytes + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > self.limits.max_bytes:
                        raise WebFetchError(
                            "too_large", "网页超过允许大小，请上传文件或粘贴所需原文"
                        )
                if lengths and len(body) != int(lengths[0]):
                    raise WebFetchError("invalid_response", "网页内容传输不完整，请重试")
                request.remaining()
                return FetchedWebPage(
                    initial.url,
                    target.url,
                    response.headers.get("Content-Type", "text/plain"),
                    bytes(body),
                )
            finally:
                connection.close()
        raise AssertionError("redirect loop exhausted without a result")
