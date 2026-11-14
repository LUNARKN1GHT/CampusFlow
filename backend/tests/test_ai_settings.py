"""Issue #553：桌面版 AI 服务配置入口（/api/v1/ai-settings）。

覆盖：非桌面只读、桌面读写、密钥不落响应、保存后状态更新、缺对应供应方密钥
拒绝、清除密钥、启动加载与环境变量优先。凭据存储使用内存实现，不写明文文件、
不访问系统钥匙串。
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from campusflow.application.ai_settings import (
    AI_DEEPSEEK_API_KEY_ACCOUNT,
    AI_PREFERENCE_FILENAME,
    AI_ZHIPU_API_KEY_ACCOUNT,
    apply_stored_ai_settings,
    build_status,
    update_ai_settings,
)
from campusflow.core.config import Settings
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.secrets.memory import InMemorySecretStore

ZHIPU_KEY = "zhipu-secret-value-0123456789"
DEEPSEEK_KEY = "deepseek-secret-value-0123456789"


def _desktop_settings(data_dir: Path, **overrides: object) -> Settings:
    """构造桌面配置；数据目录由调用方决定是否创建（用于验证"目录不存在"分支）。"""
    values: dict[str, object] = {
        "_env_file": None,
        "deployment_mode": "desktop",
        "data_dir": str(data_dir),
        "secret_backend": "file",
        "recovery_interval_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def desktop_client(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Settings, InMemorySecretStore, Path]]:
    """在既有会话上切换到桌面配置；凭据存储注入内存实现，避免明文文件与钥匙串。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = _desktop_settings(data_dir)
    store = InMemorySecretStore()
    monkeypatch.setattr(client.app.state, "settings", settings, raising=False)
    monkeypatch.setattr(client.app.state, "ai_secret_store", store, raising=False)
    monkeypatch.setattr(client.app.state, "ai_restart_required", False, raising=False)
    yield client, settings, store, data_dir


def _preference_path(data_dir: Path) -> Path:
    return data_dir / AI_PREFERENCE_FILENAME


def test_server_mode_reports_environment_configuration(client: TestClient) -> None:
    response = client.get("/api/v1/ai-settings")

    assert response.status_code == 200
    body = response.json()
    assert body["editable"] is False
    assert body["secret_backend"] == "environment"
    assert body["restart_required"] is False
    assert body["zhipu_configured"] is False
    assert body["deepseek_configured"] is False
    assert body["text_available"] is False
    assert body["vision_available"] is False
    assert body["embedding_available"] is False
    # 只读视图要说明配置来源，不能显示成"可编辑但没填"。
    assert any("环境变量" in warning for warning in body["warnings"])
    # 不得出现任何密钥字段名或取值。
    assert "zhipu_api_key" not in response.text
    assert "deepseek_api_key" not in response.text


def test_server_mode_rejects_put(client: TestClient) -> None:
    response = client.put("/api/v1/ai-settings", json={"zhipu_api_key": ZHIPU_KEY})

    assert response.status_code == 403
    assert "环境变量" in response.json()["detail"]
    assert ZHIPU_KEY not in response.text


def test_desktop_put_saves_key_without_echoing_it(
    desktop_client: tuple[TestClient, Settings, InMemorySecretStore, Path],
) -> None:
    client, settings, store, data_dir = desktop_client

    response = client.put("/api/v1/ai-settings", json={"zhipu_api_key": ZHIPU_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["editable"] is True
    assert body["text_provider"] == "zhipu"
    assert body["zhipu_configured"] is True
    assert body["text_available"] is True
    assert body["vision_available"] is True
    assert body["embedding_available"] is True
    assert body["restart_required"] is True
    assert body["secret_backend"] == "memory"
    assert ZHIPU_KEY not in response.text
    assert "zhipu_api_key" not in response.text

    # 密钥只进凭据存储；API 进程内的 Settings 立即使用新值。
    assert store.get(AI_ZHIPU_API_KEY_ACCOUNT) == ZHIPU_KEY
    assert settings.zhipu_api_key == ZHIPU_KEY
    # 只提交密钥时不该顺手写供应方偏好文件。
    assert not _preference_path(data_dir).exists()

    # 保存后重新读取：状态与"需要重启"提示都保留，仍不回显密钥。
    again = client.get("/api/v1/ai-settings")
    assert again.status_code == 200
    assert again.json()["zhipu_configured"] is True
    assert again.json()["restart_required"] is True
    assert ZHIPU_KEY not in again.text


def test_desktop_provider_switch_requires_matching_key(
    desktop_client: tuple[TestClient, Settings, InMemorySecretStore, Path],
) -> None:
    client, settings, store, data_dir = desktop_client
    client.put("/api/v1/ai-settings", json={"zhipu_api_key": ZHIPU_KEY})

    rejected = client.put("/api/v1/ai-settings", json={"text_provider": "deepseek"})

    assert rejected.status_code == 400
    assert "DeepSeek" in rejected.json()["detail"]
    # 校验失败不落库：供应方与偏好文件都不改。
    assert settings.text_provider == "zhipu"
    assert not _preference_path(data_dir).exists()

    accepted = client.put(
        "/api/v1/ai-settings",
        json={"text_provider": "deepseek", "deepseek_api_key": DEEPSEEK_KEY},
    )

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["text_provider"] == "deepseek"
    assert body["deepseek_configured"] is True
    assert body["text_available"] is True
    # 图片识别与向量检索仍只认 ZHIPU 密钥。
    assert body["vision_available"] is True
    assert body["embedding_available"] is True
    assert DEEPSEEK_KEY not in accepted.text
    assert store.get(AI_DEEPSEEK_API_KEY_ACCOUNT) == DEEPSEEK_KEY
    assert settings.text_provider == "deepseek"

    preference = _preference_path(data_dir)
    assert json.loads(preference.read_text(encoding="utf-8")) == {"text_provider": "deepseek"}
    if os.name != "nt":
        assert stat.S_IMODE(preference.stat().st_mode) == 0o600
    assert ZHIPU_KEY not in preference.read_text(encoding="utf-8")
    assert DEEPSEEK_KEY not in preference.read_text(encoding="utf-8")


def test_desktop_clear_key_updates_availability(
    desktop_client: tuple[TestClient, Settings, InMemorySecretStore, Path],
) -> None:
    client, settings, store, _data_dir = desktop_client
    client.put("/api/v1/ai-settings", json={"zhipu_api_key": ZHIPU_KEY})

    response = client.put(
        "/api/v1/ai-settings",
        json={
            "text_provider": "deepseek",
            "deepseek_api_key": DEEPSEEK_KEY,
            "clear_zhipu_api_key": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["zhipu_configured"] is False
    assert body["deepseek_configured"] is True
    assert body["text_available"] is True
    # 清除 ZHIPU 密钥后图片识别与向量检索不可用，状态必须如实反映。
    assert body["vision_available"] is False
    assert body["embedding_available"] is False
    assert store.get(AI_ZHIPU_API_KEY_ACCOUNT) is None
    assert settings.zhipu_api_key is None


def test_desktop_rejects_clearing_key_still_required(
    desktop_client: tuple[TestClient, Settings, InMemorySecretStore, Path],
) -> None:
    client, settings, store, _data_dir = desktop_client
    client.put("/api/v1/ai-settings", json={"zhipu_api_key": ZHIPU_KEY})

    response = client.put("/api/v1/ai-settings", json={"clear_zhipu_api_key": True})

    assert response.status_code == 400
    assert "ZHIPU" in response.json()["detail"]
    assert store.get(AI_ZHIPU_API_KEY_ACCOUNT) == ZHIPU_KEY
    assert settings.zhipu_api_key == ZHIPU_KEY


def test_desktop_rejects_empty_or_conflicting_key(
    desktop_client: tuple[TestClient, Settings, InMemorySecretStore, Path],
) -> None:
    client, _settings, store, _data_dir = desktop_client

    empty = client.put("/api/v1/ai-settings", json={"zhipu_api_key": "  "})
    assert empty.status_code == 400
    assert "不能为空" in empty.json()["detail"]

    conflicting = client.put(
        "/api/v1/ai-settings",
        json={"zhipu_api_key": ZHIPU_KEY, "clear_zhipu_api_key": True},
    )
    assert conflicting.status_code == 400
    assert store.get(AI_ZHIPU_API_KEY_ACCOUNT) is None


def test_desktop_rejects_unknown_fields(
    desktop_client: tuple[TestClient, Settings, InMemorySecretStore, Path],
) -> None:
    client, _settings, _store, _data_dir = desktop_client

    response = client.put("/api/v1/ai-settings", json={"zhipu_api_key": ZHIPU_KEY, "model": "x"})

    assert response.status_code == 422
    assert ZHIPU_KEY not in response.text


def test_status_never_returns_key_material(
    desktop_client: tuple[TestClient, Settings, InMemorySecretStore, Path],
) -> None:
    client, _settings, _store, _data_dir = desktop_client
    client.put("/api/v1/ai-settings", json={"zhipu_api_key": ZHIPU_KEY})

    body = client.get("/api/v1/ai-settings").text

    assert ZHIPU_KEY not in body
    for fragment in (ZHIPU_KEY[:6], ZHIPU_KEY[-6:]):
        assert fragment not in body


def test_startup_applies_stored_values_and_keeps_environment_first(tmp_path: Path) -> None:
    store = InMemorySecretStore()
    store.set(AI_ZHIPU_API_KEY_ACCOUNT, "stored-zhipu")
    store.set(AI_DEEPSEEK_API_KEY_ACCOUNT, "stored-deepseek")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / AI_PREFERENCE_FILENAME).write_text(
        json.dumps({"text_provider": "deepseek"}), encoding="utf-8"
    )

    # 环境变量（这里用显式构造值模拟）已给出 ZHIPU 密钥：存储值不能覆盖它。
    settings = _desktop_settings(data_dir, zhipu_api_key="env-zhipu")
    warnings = apply_stored_ai_settings(settings, secret_store=store)

    assert warnings == []
    assert settings.zhipu_api_key == "env-zhipu"
    assert settings.deepseek_api_key == "stored-deepseek"
    assert settings.text_provider == "deepseek"


def test_startup_is_noop_outside_desktop_mode(tmp_path: Path) -> None:
    store = InMemorySecretStore()
    store.set(AI_ZHIPU_API_KEY_ACCOUNT, "stored-zhipu")
    server = Settings(_env_file=None)

    assert apply_stored_ai_settings(server, secret_store=store) == []
    assert server.zhipu_api_key is None

    # 桌面模式但数据目录还不存在（首启之前）：不读取存储、不报错。
    missing = _desktop_settings(tmp_path / "not-there")
    assert apply_stored_ai_settings(missing, secret_store=store) == []
    assert missing.zhipu_api_key is None


def test_corrupt_preference_falls_back_with_warning(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / AI_PREFERENCE_FILENAME).write_text("{not json", encoding="utf-8")
    settings = _desktop_settings(data_dir)

    warnings = apply_stored_ai_settings(settings, secret_store=InMemorySecretStore())

    assert settings.text_provider == "zhipu"
    assert any("回退默认" in warning for warning in warnings)
    status = build_status(settings, secret_store=InMemorySecretStore())
    assert any("回退默认" in warning for warning in status.warnings)


def test_unknown_stored_provider_is_ignored(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / AI_PREFERENCE_FILENAME).write_text(
        json.dumps({"text_provider": "other"}), encoding="utf-8"
    )
    settings = _desktop_settings(data_dir)

    warnings = apply_stored_ai_settings(settings, secret_store=InMemorySecretStore())

    assert settings.text_provider == "zhipu"
    assert warnings  # 取值无效也要给出提示，不能静默


def test_update_rejects_non_desktop_settings(tmp_path: Path) -> None:
    for settings in (
        Settings(_env_file=None),
        Settings.model_construct(deployment_mode="desktop", data_dir=None, secret_backend="file"),
    ):
        with pytest.raises(DomainError):
            update_ai_settings(settings, zhipu_api_key="key", secret_store=InMemorySecretStore())


def test_status_reports_unavailable_secret_backend(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = _desktop_settings(data_dir)

    def fail(settings: Settings) -> None:
        raise RuntimeError("no store")

    monkeypatch.setattr("campusflow.application.ai_settings.open_ai_secret_store", fail)

    status = build_status(settings)

    assert status.secret_backend == "unavailable"
    assert any("凭据存储不可用" in warning for warning in status.warnings)
