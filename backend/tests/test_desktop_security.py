"""P012：桌面模式 Host／Origin 防护、安全响应头与 HTML CSP。

全部用例不访问数据库：登录接口只使用进程内令牌集合。桌面模式客户端显式使用
`base_url="http://127.0.0.1"`，让 Host 校验真正生效，而不是被默认的 testserver 绕过。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from campusflow.api.desktop_security import (
    CONTENT_SECURITY_POLICY,
    HOST_REJECTED_MESSAGE,
    ORIGIN_REJECTED_MESSAGE,
    is_allowed_host,
    is_allowed_origin,
)
from campusflow.core.config import Settings
from campusflow.main import create_app

INDEX_MARKER = "spa-index-marker"


def _write_dist(root: Path) -> None:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(
        f'<!doctype html><div id="app">{INDEX_MARKER}</div>', encoding="utf-8"
    )
    (root / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")
    # 主题引导脚本已从 index.html 内联脚本外置，CSP 的 script-src 'self' 才能放行。
    (root / "theme-boot.js").write_text("console.log('theme')", encoding="utf-8")
    (assets / "index-abc123.js").write_text("console.log('campusflow')", encoding="utf-8")


def _desktop_settings(tmp_path: Path, static_dir: Path | None = None) -> Settings:
    return Settings(
        _env_file=None,
        deployment_mode="desktop",
        data_dir=str(tmp_path / "data"),
        static_dir=str(static_dir) if static_dir is not None else None,
    )


@pytest.fixture
def desktop_client(tmp_path: Path) -> TestClient:
    dist = tmp_path / "dist"
    _write_dist(dist)
    return TestClient(
        create_app(_desktop_settings(tmp_path, static_dir=dist)),
        base_url="http://127.0.0.1",
    )


@pytest.fixture
def desktop_api_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(_desktop_settings(tmp_path)), base_url="http://127.0.0.1")


@pytest.fixture
def server_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None)))


def _login(client: TestClient, *, username: str, password: str):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


# ------------------------------------------------------------------ 主机与来源解析


@pytest.mark.parametrize(
    "host",
    (
        "127.0.0.1",
        "127.0.0.1:8000",
        "localhost",
        "localhost:5173",
        "LOCALHOST:8080",
        "[::1]",
        "[::1]:8000",
        "::1",
    ),
)
def test_allowed_host_variants(host: str) -> None:
    assert is_allowed_host(host), host


@pytest.mark.parametrize(
    "host",
    (
        "",
        "evil.example",
        "evil.example:8000",
        "127.0.0.1.evil.com",
        "localhost.evil.com",
        "127.0.0.1.",
        "localhost:abc",
        "localhost:99999",
        "localhost:0",
        "[::1",
        "::2",
        "127.0.0.1:8000:9000",
        "user@localhost",
        " 127.0.0.1",
    ),
)
def test_malformed_or_external_hosts_are_rejected_by_parser(host: str) -> None:
    assert not is_allowed_host(host), host


@pytest.mark.parametrize(
    "origin",
    (
        "http://127.0.0.1",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://LOCALHOST",
        "http://[::1]:8000",
    ),
)
def test_loopback_origins_are_allowed(origin: str) -> None:
    assert is_allowed_origin(origin), origin


@pytest.mark.parametrize(
    "origin",
    (
        "",
        "null",
        "http://evil.example",
        "https://127.0.0.1",
        "http://localhost.evil.com",
        "http://127.0.0.1/path",
        "http://127.0.0.1:99999",
        "http://user@localhost",
        "file://",
    ),
)
def test_non_loopback_or_malformed_origins_are_rejected(origin: str) -> None:
    assert not is_allowed_origin(origin), origin


# ------------------------------------------------------------------ 服务端模式不受影响


def test_server_mode_ignores_host_and_origin_and_adds_no_headers(server_client: TestClient) -> None:
    health = server_client.get("/api/v1/health", headers={"host": "evil.example"})
    assert health.status_code == 200
    for name in ("x-content-type-options", "referrer-policy", "x-frame-options"):
        assert name not in health.headers
    assert "content-security-policy" not in health.headers

    blocked = _login(server_client, username="student", password="wrong")
    assert blocked.status_code == 401
    cross_site = server_client.post(
        "/api/v1/auth/login",
        json={"username": "student", "password": "wrong"},
        headers={"origin": "http://evil.example"},
    )
    # 服务端模式不做 Origin 拦截，请求进入登录处理器后返回凭据错误。
    assert cross_site.status_code == 401
    assert cross_site.json()["detail"] == "用户名或密码错误"
    assert "content-security-policy" not in cross_site.headers


# ------------------------------------------------------------------ Host 允许列表


@pytest.mark.parametrize(
    "host",
    ("127.0.0.1", "127.0.0.1:8000", "localhost", "LOCALHOST:5173", "[::1]", "[::1]:8000"),
)
def test_desktop_allows_loopback_hosts(desktop_api_client: TestClient, host: str) -> None:
    response = desktop_api_client.get("/api/v1/health", headers={"host": host})
    assert response.status_code == 200, host
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    "host",
    (
        "evil.example",
        "evil.example:8000",
        "127.0.0.1.evil.com",
        "localhost.evil.com",
        "localhost:abc",
        "127.0.0.1:99999",
        "[::1",
        "user@localhost",
    ),
)
def test_desktop_rejects_external_and_malformed_hosts(
    desktop_api_client: TestClient, host: str
) -> None:
    response = desktop_api_client.get("/api/v1/health", headers={"host": host})
    assert response.status_code == 400, host
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": HOST_REJECTED_MESSAGE}
    # 拒绝响应同样带基础安全头，且不因拒绝泄漏 HTML。
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in response.headers


def _call_asgi(
    app, *, headers: list[tuple[bytes, bytes]], method: str = "GET", path: str = "/api/v1/health"
) -> list[dict]:
    """绕过 TestClient 的 Host 自动补全，直接观察中间件对原始头的处理。"""
    import anyio

    messages: list[dict] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    anyio.run(app, scope, receive, send)
    return messages


def _response_body(messages: list[dict]) -> bytes:
    return b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")


def test_desktop_rejects_request_without_host(tmp_path: Path) -> None:
    # TestClient 总会补 Host，这里直接用最小 ASGI 调用验证缺失 Host 的分支。
    app = create_app(_desktop_settings(tmp_path / "missing-host"))
    messages = _call_asgi(app, headers=[])
    assert messages[0]["status"] == 400
    assert HOST_REJECTED_MESSAGE.encode("utf-8") in _response_body(messages)


def test_desktop_rejects_duplicate_host_headers(tmp_path: Path) -> None:
    # 多个 Host 头可能被用于代理绕过；必须整体拒绝而不是取第一个。
    app = create_app(_desktop_settings(tmp_path / "duplicate-host"))
    messages = _call_asgi(
        app,
        headers=[(b"host", b"127.0.0.1"), (b"host", b"evil.example")],
    )
    assert messages[0]["status"] == 400
    assert HOST_REJECTED_MESSAGE.encode("utf-8") in _response_body(messages)


def test_desktop_x_forwarded_host_cannot_bypass(
    desktop_api_client: TestClient,
) -> None:
    rejected = desktop_api_client.get(
        "/api/v1/health",
        headers={"host": "evil.example", "x-forwarded-host": "127.0.0.1"},
    )
    assert rejected.status_code == 400

    accepted = desktop_api_client.get(
        "/api/v1/health",
        headers={"host": "127.0.0.1", "x-forwarded-host": "evil.example"},
    )
    assert accepted.status_code == 200


# ------------------------------------------------------------------ Origin 与写入请求


@pytest.mark.parametrize(
    "origin",
    ("http://evil.example", "https://127.0.0.1", "null", "http://localhost.evil.com"),
)
def test_desktop_rejects_cross_origin_writes(desktop_api_client: TestClient, origin: str) -> None:
    response = desktop_api_client.post(
        "/api/v1/auth/login",
        json={"username": "student", "password": "campusflow-dev"},
        headers={"origin": origin},
    )
    assert response.status_code == 403, origin
    assert response.json() == {"detail": ORIGIN_REJECTED_MESSAGE}
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "method,path",
    (
        ("POST", "/api/v1/auth/login"),
        ("PUT", "/api/v1/tasks/1"),
        ("PATCH", "/api/v1/tasks/1"),
        ("DELETE", "/api/v1/tasks/1"),
    ),
)
def test_desktop_rejects_cross_origin_for_all_write_methods(
    desktop_api_client: TestClient, method: str, path: str
) -> None:
    response = desktop_api_client.request(
        method, path, json={}, headers={"origin": "http://evil.example"}
    )
    assert response.status_code == 403, method


@pytest.mark.parametrize(
    "origin",
    ("http://127.0.0.1:5173", "http://localhost", "http://[::1]:8000"),
)
def test_desktop_allows_loopback_origin_writes(desktop_api_client: TestClient, origin: str) -> None:
    response = _login_with_origin(desktop_api_client, origin, password="campusflow-dev")
    assert response.status_code == 200, origin
    assert response.json() == {"username": "student"}
    assert "campusflow_session" in response.cookies


def test_desktop_allows_write_without_origin(desktop_api_client: TestClient) -> None:
    # 非浏览器客户端不带 Origin：放行到处理器后仍按凭据校验。
    rejected = _login(desktop_api_client, username="student", password="wrong")
    assert rejected.status_code == 401
    accepted = _login(desktop_api_client, username="student", password="campusflow-dev")
    assert accepted.status_code == 200


def test_desktop_does_not_apply_origin_check_to_reads(desktop_api_client: TestClient) -> None:
    # 规范只拦截写入方法；GET 带外部 Origin 不改变响应，仍由会话与业务校验负责。
    response = desktop_api_client.get("/api/v1/health", headers={"origin": "http://evil.example"})
    assert response.status_code == 200


def _login_with_origin(client: TestClient, origin: str, *, password: str):
    return client.post(
        "/api/v1/auth/login",
        json={"username": "student", "password": password},
        headers={"origin": origin},
    )


# ------------------------------------------------------------------ 响应头与 CSP


def test_spa_html_has_csp_and_security_headers(desktop_client: TestClient) -> None:
    for path in ("/", "/plan", "/login?expired=1"):
        response = desktop_client.get(path)
        assert response.status_code == 200, path
        assert INDEX_MARKER in response.text
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"


def test_api_json_has_security_headers_but_no_csp(desktop_client: TestClient) -> None:
    response = desktop_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in response.headers


def test_docs_keeps_working_without_csp(desktop_client: TestClient) -> None:
    docs = desktop_client.get("/docs")
    assert docs.status_code == 200
    assert docs.headers["content-type"].startswith("text/html")
    assert "content-security-policy" not in docs.headers
    assert docs.headers["x-content-type-options"] == "nosniff"

    openapi = desktop_client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "content-security-policy" not in openapi.headers


def test_static_assets_and_404_keep_headers(desktop_client: TestClient) -> None:
    asset = desktop_client.get("/assets/index-abc123.js")
    assert asset.status_code == 200
    assert "javascript" in asset.headers["content-type"]
    assert asset.headers["x-content-type-options"] == "nosniff"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    # 脚本不是 HTML，不附加文档 CSP。
    assert "content-security-policy" not in asset.headers

    missing = desktop_client.get("/assets/index-missing.js")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not Found"}
    assert missing.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in missing.headers

    # 首屏主题脚本必须能从同源加载，且不因 CSP 被当作文档。
    theme_boot = desktop_client.get("/theme-boot.js")
    assert theme_boot.status_code == 200
    assert "javascript" in theme_boot.headers["content-type"]
    assert "content-security-policy" not in theme_boot.headers

    head = desktop_client.head("/plan")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


def test_history_fallback_and_login_flow_work_under_security(
    desktop_client: TestClient,
) -> None:
    fallback = desktop_client.get("/review/changes")
    assert fallback.status_code == 200
    assert INDEX_MARKER in fallback.text
    assert fallback.headers["content-security-policy"] == CONTENT_SECURITY_POLICY

    login = _login_with_origin(desktop_client, "http://127.0.0.1:5173", password="campusflow-dev")
    assert login.status_code == 200
    session = desktop_client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json() == {"username": "student"}
    assert "content-security-policy" not in session.headers
