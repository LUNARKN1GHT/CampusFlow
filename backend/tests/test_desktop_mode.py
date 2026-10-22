"""P004：桌面部署模式的数据目录、SPA 托管边界与迁移资源定位。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from campusflow.core.config import Settings
from campusflow.core.resources import resolve_migrations_dir
from campusflow.infrastructure.db.migrations import build_alembic_config
from campusflow.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent
INDEX_MARKER = "spa-index-marker"


def _desktop_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "deployment_mode": "desktop",
        "data_dir": str(tmp_path / "data"),
    }
    values.update(overrides)
    return Settings(**values)


def _write_dist(root: Path) -> None:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(
        f'<!doctype html><div id="app">{INDEX_MARKER}</div>', encoding="utf-8"
    )
    (root / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")
    (assets / "index-abc123.js").write_text("console.log('campusflow')", encoding="utf-8")


def test_desktop_mode_requires_absolute_data_dir(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="CAMPUSFLOW_DATA_DIR"):
        Settings(_env_file=None, deployment_mode="desktop")
    with pytest.raises(ValidationError, match="绝对路径"):
        Settings(_env_file=None, deployment_mode="desktop", data_dir="relative/data")


def test_desktop_mode_derives_storage_dir_under_data_dir(tmp_path: Path) -> None:
    settings = _desktop_settings(tmp_path)
    assert settings.deployment_mode == "desktop"
    assert settings.data_dir == str(tmp_path / "data")
    assert settings.storage_dir == str(tmp_path / "data" / "materials")

    custom = tmp_path / "custom-storage"
    overridden = _desktop_settings(tmp_path, storage_dir=str(custom))
    assert overridden.storage_dir == str(custom)


def test_server_mode_keeps_existing_defaults(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, static_dir=str(tmp_path / "dist"))
    assert settings.deployment_mode == "server"
    assert settings.storage_dir == "data/materials"
    assert settings.data_dir is None


def test_desktop_spa_serves_index_and_history_routes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_dist(dist)
    client = TestClient(
        create_app(_desktop_settings(tmp_path, static_dir=str(dist))),
        base_url="http://127.0.0.1",
    )

    for path in ("/", "/login?expired=1", "/review/changes"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert INDEX_MARKER in response.text
        assert response.headers["cache-control"] == "no-cache"

    head = client.head("/plan")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["cache-control"] == "no-cache"


def test_desktop_spa_serves_hashed_assets_and_missing_asset_is_404(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_dist(dist)
    client = TestClient(
        create_app(_desktop_settings(tmp_path, static_dir=str(dist))),
        base_url="http://127.0.0.1",
    )

    asset = client.get("/assets/index-abc123.js")
    assert asset.status_code == 200
    assert asset.text == "console.log('campusflow')"
    assert "javascript" in asset.headers["content-type"]
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

    head = client.head("/assets/index-abc123.js")
    assert head.status_code == 200
    assert head.content == b""

    missing = client.get("/assets/index-missing.js")
    assert missing.status_code == 404
    assert "text/html" not in missing.headers["content-type"]
    assert INDEX_MARKER not in missing.text

    favicon = client.get("/favicon.svg")
    assert favicon.status_code == 200
    assert favicon.headers["cache-control"] == "no-cache"
    assert favicon.text == "<svg></svg>"


def test_desktop_spa_never_swallows_api_paths(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_dist(dist)
    client = TestClient(
        create_app(_desktop_settings(tmp_path, static_dir=str(dist))),
        base_url="http://127.0.0.1",
    )

    unknown = client.get("/api/v1/no-such-route")
    assert unknown.status_code == 404
    assert unknown.headers["content-type"].startswith("application/json")
    assert unknown.json() == {"detail": "Not Found"}

    bare_api = client.get("/api")
    assert bare_api.status_code == 404
    assert bare_api.headers["content-type"].startswith("application/json")

    posted = client.post("/api/v1/no-such-route", json={})
    assert posted.status_code == 404
    assert posted.headers["content-type"].startswith("application/json")
    assert posted.json() == {"detail": "Not Found"}

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "campusflow-api"}


def test_server_mode_does_not_mount_spa_even_with_static_dir(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_dist(dist)
    settings = Settings(_env_file=None, static_dir=str(dist))
    client = TestClient(create_app(settings))

    assert client.get("/").status_code == 404
    assert client.get("/plan").status_code == 404
    assert client.get("/api/v1/health").status_code == 200
    unknown = client.get("/api/v1/no-such-route")
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Not Found"}


def test_desktop_mode_without_static_dir_serves_api_only(tmp_path: Path) -> None:
    client = TestClient(create_app(_desktop_settings(tmp_path)), base_url="http://127.0.0.1")
    assert client.get("/").status_code == 404
    assert client.get("/api/v1/health").status_code == 200


def test_desktop_mode_rejects_static_dir_without_index(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="index.html"):
        create_app(_desktop_settings(tmp_path, static_dir=str(tmp_path / "missing-dist")))


def test_resolve_migrations_dir_accepts_explicit_path(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    assert resolve_migrations_dir(str(migrations)) == migrations.resolve()
    with pytest.raises(FileNotFoundError, match="迁移目录不存在"):
        resolve_migrations_dir(str(tmp_path / "missing"))


def test_resolve_migrations_dir_falls_back_to_repository() -> None:
    resolved = resolve_migrations_dir()
    assert resolved == (BACKEND_DIR / "migrations").resolve()
    assert (resolved / "env.py").is_file()


def test_build_alembic_config_sets_absolute_script_location(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    config = build_alembic_config(str(migrations))
    assert Path(config.get_main_option("script_location")) == migrations.resolve()
