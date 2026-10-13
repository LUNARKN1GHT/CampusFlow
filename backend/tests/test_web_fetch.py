"""N004：真实 HTTP 响应解析配合可控的 DNS/socket，不访问外部网站。"""

import io
import socket
import ssl
import threading
import time
from collections import deque
from pathlib import Path
from unittest.mock import Mock

import pytest

from campusflow.application.ports.web_fetch import WebFetchError
from campusflow.infrastructure import web_fetch as web

PUBLIC_IP = "93.184.216.34"


def addresses(ip=PUBLIC_IP, port=80):
    if ":" in ip:
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def response(body=b"hello", *, status=200, headers=()):
    fields = [("Content-Type", "text/html; charset=utf-8"), *headers]
    return (
        f"HTTP/1.1 {status} Result\r\n".encode()
        + b"".join(f"{key}: {value}\r\n".encode() for key, value in fields)
        + b"\r\n"
        + body
    )


class FakeSocket:
    def __init__(self, raw):
        self.raw = raw
        self.sent = bytearray()
        self.destination = None
        self.closed = False

    def connect(self, destination):
        self.destination = destination

    def settimeout(self, timeout):
        assert timeout > 0

    def sendall(self, data):
        self.sent.extend(data)

    def makefile(self, mode):
        return io.BytesIO(self.raw)

    def shutdown(self, how):
        self.closed = True

    def close(self):
        self.closed = True


@pytest.fixture
def wire(monkeypatch):
    pending = deque()
    sockets = []

    def make_socket(*args):
        sock = FakeSocket(pending.popleft())
        sockets.append(sock)
        return sock

    resolver = Mock(side_effect=lambda host, port, **kwargs: addresses(port=port))
    monkeypatch.setattr(web.socket, "getaddrinfo", resolver)
    monkeypatch.setattr(web.socket, "socket", make_socket)
    return pending, sockets, resolver


def assert_error(url, code, limits=None):
    with pytest.raises(WebFetchError) as error:
        web.PublicWebFetcher(limits).fetch(url)
    assert error.value.code == code


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a",
        "gopher://example.com/",
        "javascript:alert(1)",
        "//example.com/a",
        "http://u:p@example.com",
        "http://example.com:8080",
        "https://example.com:80",
        "http://example.com:0",
        "http://example.com:bad",
        "http://example.com\\@127.0.0.1",
        " http://example.com",
        "http://example.com/\r\nX:1",
        "http://[fe80::1%25eth0]/",
        "",
        "http://example.com/" + "a" * 8192,
    ],
)
def test_invalid_urls_do_not_resolve_or_connect(wire, url):
    _, sockets, resolver = wire
    assert_error(url, "invalid_url")
    resolver.assert_not_called()
    assert not sockets


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST.",
        "printer.local",
        "api.internal",
        "127.0.0.1",
        "0.0.0.0",
        "10.2.3.4",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "192.0.0.8",
        "224.0.0.1",
        "255.255.255.255",
        "168.63.129.16",
        "[::1]",
        "[::]",
        "[fc00::1]",
        "[fe80::1]",
        "[ff02::1]",
        "[::ffff:127.0.0.1]",
        "[64:ff9b::a00:1]",
        "[2002:7f00:1::1]",
        "[2001::1]",
        "[2001:db8::1]",
    ],
)
def test_private_literals_are_rejected_before_dns(wire, host):
    _, sockets, resolver = wire
    assert_error(f"http://{host}/", "blocked_address")
    resolver.assert_not_called()
    assert not sockets


@pytest.mark.parametrize("host", ["127.1", "2130706433", "0x7f000001", "example.com"])
def test_private_dns_results_including_alternative_ip_spellings(wire, host):
    _, sockets, resolver = wire
    resolver.side_effect = lambda *args, **kwargs: addresses("127.0.0.1")
    assert_error(f"http://{host}/", "blocked_address")
    assert not sockets


def test_mixed_public_and_private_dns_answers_reject_entire_target(wire):
    _, sockets, resolver = wire
    resolver.side_effect = lambda *args, **kwargs: addresses() + addresses("10.0.0.1")
    assert_error("http://example.com", "blocked_address")
    assert not sockets


@pytest.mark.parametrize("location", ["http://127.0.0.1/", "//10.0.0.1/", "file:///etc/passwd"])
def test_redirect_cannot_enter_private_network_or_change_protocol(wire, location):
    pending, sockets, _ = wire
    pending.append(response(status=302, headers=[("Location", location)]))
    assert_error(
        "http://example.com", "invalid_url" if location.startswith("file:") else "blocked_address"
    )
    assert len(sockets) == 1
    assert sockets[0].closed


def test_redirect_to_hostname_resolving_private_is_blocked(wire):
    pending, sockets, resolver = wire
    pending.append(response(status=302, headers=[("Location", "http://private.example/path")]))
    resolver.side_effect = [addresses(), addresses("192.168.0.2")]
    assert_error("http://example.com", "blocked_address")
    assert len(sockets) == 1


def test_explicit_fetch_preserves_sample_and_does_not_crawl_or_send_cookies(wire, monkeypatch):
    pending, sockets, resolver = wire
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8888")
    body = (Path(__file__).parents[2] / "samples/synthetic/n001/public-notice.html").read_bytes()
    pending.extend(
        [
            response(
                status=302, headers=[("Location", "/notice?term=1"), ("Set-Cookie", "secret=1")]
            ),
            response(body, headers=[("Content-Length", str(len(body)))]),
        ]
    )
    fetcher = web.PublicWebFetcher()
    resolver.assert_not_called()  # 创建组件不触发后台读取。
    result = fetcher.fetch("http://example.com/start#section")
    assert result.body == body
    assert result.requested_url == "http://example.com/start"
    assert result.final_url == "http://example.com/notice?term=1"
    assert len(sockets) == 2
    assert resolver.call_count == 2
    assert all(sock.destination == (PUBLIC_IP, 80) and sock.closed for sock in sockets)
    assert b"GET /notice?term=1 HTTP/1.1" in sockets[1].sent
    assert b"Host: example.com" in sockets[1].sent
    assert b"Cookie:" not in sockets[1].sent
    assert b"Authorization:" not in sockets[1].sent


def test_checked_address_is_pinned_without_second_dns_lookup(wire):
    pending, sockets, resolver = wire
    pending.append(response())
    resolver.side_effect = [addresses(), addresses("127.0.0.1")]
    web.PublicWebFetcher().fetch("http://example.com")
    assert resolver.call_count == 1
    assert sockets[0].destination == (PUBLIC_IP, 80)


def test_same_host_redirect_is_checked_again_if_dns_changes(wire):
    pending, sockets, resolver = wire
    pending.append(response(status=302, headers=[("Location", "/next")]))
    resolver.side_effect = [addresses(), addresses("127.0.0.1")]
    assert_error("http://example.com", "blocked_address")
    assert resolver.call_count == 2
    assert len(sockets) == 1


def test_public_ipv6_is_supported(wire):
    pending, sockets, resolver = wire
    pending.append(response())
    resolver.side_effect = lambda *args, **kwargs: addresses("2606:4700:4700::1111")
    assert web.PublicWebFetcher().fetch("http://example.com").body == b"hello"
    assert sockets[0].destination == ("2606:4700:4700::1111", 80, 0, 0)


def test_size_boundary_succeeds_without_following_embedded_links(wire):
    pending, sockets, _ = wire
    body = b'<a href="http://127.0.0.1/">local</a>'
    pending.append(response(body))
    result = web.PublicWebFetcher(web.FetchLimits(max_bytes=len(body))).fetch("http://example.com")
    assert result.body == body
    assert len(sockets) == 1


def test_https_uses_default_certificate_verification_and_original_hostname(wire, monkeypatch):
    pending, sockets, _ = wire
    pending.append(response())
    context = ssl.create_default_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    context_factory = Mock()

    def wrap(sock, **kwargs):
        assert kwargs == {"server_hostname": "example.com", "do_handshake_on_connect": False}
        sock.do_handshake = Mock()
        return sock

    context_factory.return_value.wrap_socket.side_effect = wrap
    monkeypatch.setattr(web.ssl, "create_default_context", context_factory)
    web.PublicWebFetcher().fetch("https://example.com")
    context_factory.assert_called_once_with()
    sockets[0].do_handshake.assert_called_once_with()
    assert sockets[0].destination == (PUBLIC_IP, 443)


def test_bad_tls_certificate_does_not_fall_back_to_insecure_http(wire, monkeypatch):
    pending, sockets, _ = wire
    pending.append(response())
    factory = Mock()
    factory.return_value.wrap_socket.side_effect = ssl.SSLCertVerificationError("private detail")
    monkeypatch.setattr(web.ssl, "create_default_context", factory)
    assert_error("https://example.com", "unreachable")
    assert len(sockets) == 1
    assert sockets[0].closed


def test_binary_response_is_rejected(wire):
    pending, _, _ = wire
    pending.append(b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\n\r\n%PDF-1.7")
    assert_error("http://example.com", "unsupported_type")


@pytest.mark.parametrize(
    "headers,body",
    [
        ([("Content-Length", "100")], b""),
        ([], b"01234567890"),
        ([("Transfer-Encoding", "chunked")], b"B\r\n01234567890\r\n0\r\n\r\n"),
    ],
)
def test_declared_streamed_and_chunked_bodies_are_bounded(wire, headers, body):
    pending, sockets, _ = wire
    pending.append(response(body, headers=headers))
    assert_error("http://example.com", "too_large", web.FetchLimits(max_bytes=10))
    assert sockets[0].closed


@pytest.mark.parametrize(
    "headers,code",
    [
        ([("Content-Length", "bad")], "invalid_response"),
        ([("Content-Length", "-1")], "invalid_response"),
        ([("Content-Length", "5"), ("Content-Length", "6")], "invalid_response"),
        ([("Content-Length", "10")], "invalid_response"),
        ([("Content-Encoding", "gzip")], "unsupported_encoding"),
    ],
)
def test_invalid_or_compressed_responses_do_not_return_partial_content(wire, headers, code):
    pending, _, _ = wire
    pending.append(response(headers=headers))
    assert_error("http://example.com", code)


@pytest.mark.parametrize(
    "status,code",
    [(401, "access_denied"), (403, "access_denied"), (404, "http_status"), (500, "http_status")],
)
def test_non_public_or_failed_pages_are_clear_failures(wire, status, code):
    pending, _, _ = wire
    pending.append(response(status=status))
    assert_error("http://example.com", code)


def test_redirect_limit_and_cycle(wire):
    pending, _, _ = wire
    pending.append(response(status=302, headers=[("Location", "/next")]))
    assert_error("http://example.com", "redirect_limit", web.FetchLimits(max_redirects=0))
    pending.append(response(status=302, headers=[("Location", "/")]))
    assert_error("http://example.com", "redirect_limit")


def test_dns_timeout_returns_promptly_and_never_connects_after_cancellation(wire, monkeypatch):
    _, sockets, resolver = wire
    unblock = threading.Event()
    finished = threading.Event()
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(web, "_SLOTS", slots)

    def resolve(*args, **kwargs):
        unblock.wait(2)
        finished.set()
        return addresses()

    resolver.side_effect = resolve
    start = time.monotonic()
    try:
        assert_error("http://example.com", "timeout", web.FetchLimits(timeout_seconds=0.05))
        assert time.monotonic() - start < 1
        assert_error("http://example.com", "busy")
    finally:
        unblock.set()
    assert finished.wait(1)
    assert slots.acquire(timeout=1)
    slots.release()
    assert not sockets


def test_slow_headers_obey_total_deadline_and_close_socket(wire, monkeypatch):
    pending, sockets, _ = wire
    pending.append(response())
    released = threading.Event()
    real_makefile = FakeSocket.makefile
    real_shutdown = FakeSocket.shutdown

    def makefile(self, mode):
        released.wait(2)
        return real_makefile(self, mode)

    def shutdown(self, how):
        released.set()
        real_shutdown(self, how)

    monkeypatch.setattr(FakeSocket, "makefile", makefile)
    monkeypatch.setattr(FakeSocket, "shutdown", shutdown)
    assert_error("http://example.com", "timeout", web.FetchLimits(timeout_seconds=0.05))
    assert released.is_set()
    assert sockets[0].closed
