"""#553：配置保密、跨调用生效和分能力检查，不连接数据库或真实模型。"""

import json
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from campusflow.api import ai_settings as api
from campusflow.application.ai_settings import AISettingsUpdate, public_settings, save_settings
from campusflow.core.config import Settings
from campusflow.domain.errors import DomainError, ModelOutputError, PermanentError, TransientError
from campusflow.infrastructure.ai_settings import resolve_ai_settings, settings_store
from campusflow.main import create_app
from campusflow.workers import assembly, embeddings, generation


@pytest.fixture
def base(tmp_path):
    return Settings(
        _env_file=None,
        deployment_mode="desktop",
        data_dir=str(tmp_path),
        secret_backend="file",
        ZHIPU_API_KEY=None,
        DEEPSEEK_API_KEY=None,
    )


def update(**kwargs):
    return AISettingsUpdate(
        text_provider="zhipu",
        text_model="glm-4-flash-250414",
        vision_model="glm-4v-flash",
        **kwargs,
    )


def persist(base, **kwargs):
    store, account = settings_store(base)
    return save_settings(base, store, account, update(**kwargs))


@pytest.fixture
def api_client(base):
    app = create_app(base)
    # 不进入 lifespan：这些已登录配置接口不需要数据库。
    client = TestClient(app, base_url="http://127.0.0.1")
    yield client
    client.close()


def login(client):
    response = client.post(
        "/api/v1/auth/login", json={"username": "student", "password": "campusflow-dev"}
    )
    assert response.status_code == 200


def test_persistence_replace_clear_and_no_environment_fallback(base):
    base.zhipu_api_key = "synthetic-environment-key"
    persist(base, zhipu_api_key=SecretStr("synthetic-new-key"))
    assert resolve_ai_settings(base).zhipu_api_key == "synthetic-new-key"
    persist(base)  # 留空保留
    assert resolve_ai_settings(base).zhipu_api_key == "synthetic-new-key"
    persist(base, zhipu_api_key=SecretStr(""))
    assert not resolve_ai_settings(base).zhipu_api_key
    restarted = Settings(
        _env_file=None,
        deployment_mode="desktop",
        data_dir=base.data_dir,
        secret_backend="file",
        ZHIPU_API_KEY="synthetic-environment-key",
    )
    assert not resolve_ai_settings(restarted).zhipu_api_key
    assert "synthetic" not in json.dumps(public_settings(restarted))


def test_auth_validation_and_public_response_do_not_echo_keys(api_client):
    path = "/api/v1/settings/ai"
    assert api_client.get(path).status_code == 401
    login(api_client)
    values = {
        "text_provider": "zhipu",
        "text_model": "glm-4-flash-250414",
        "vision_model": "glm-4v-flash",
        "zhipu_api_key": "synthetic-secret",
    }
    response = api_client.put(path, json=values)
    assert response.status_code == 200
    assert response.json()["capabilities"] == {"text": True, "vision": True, "embedding": True}
    assert "synthetic-secret" not in response.text + api_client.get(path).text
    values["zhipu_api_key"] = "synthetic-secret with spaces"
    rejected = api_client.put(path, json=values)
    assert rejected.status_code == 422
    assert "synthetic-secret" not in rejected.text


def test_corrupt_store_does_not_fall_back_or_expose_content(base):
    store, account = settings_store(base)
    store.set(account, "synthetic-secret-corrupt-json")
    with pytest.raises(DomainError, match="无法读取") as caught:
        resolve_ai_settings(base)
    assert "synthetic-secret" not in str(caught.value)


def test_factories_and_long_running_vision_reload_keys(base, monkeypatch):
    seen = []

    class Adapter:
        def __init__(self, key, **kwargs):
            seen.append(key)

        def parse(self, *args):
            return "synthetic-result"

    monkeypatch.setattr(assembly, "ZhipuVisionParser", Adapter)
    monkeypatch.setattr(embeddings, "ZhipuEmbedder", Adapter)
    monkeypatch.setattr(generation, "ZhipuTextGenerator", Adapter)
    monkeypatch.setattr(embeddings, "_FACTORY", assembly.build_desktop_embedder)
    vision = assembly.DesktopVisionParser(base)
    for key in ("synthetic-first", "synthetic-second"):
        persist(base, zhipu_api_key=SecretStr(key))
        vision.parse(b"fake", "image/png")
        embeddings.make_embedder(base)
        generation.build_text_generator(base)
        assert seen[-3:] == [key] * 3
    persist(base, zhipu_api_key=SecretStr(""))
    with pytest.raises(PermanentError, match="未配置"):
        vision.parse(b"fake", "image/png")
    with pytest.raises(PermanentError, match="未配置"):
        embeddings.make_embedder(base).embed("synthetic")
    with pytest.raises(ValueError, match="缺少"):
        generation.build_text_generator(base)


@pytest.mark.parametrize(
    "failure,code",
    [
        (PermanentError("HTTP 401 synthetic-secret"), "credentials"),
        (PermanentError("HTTP 403"), "credentials"),
        (TransientError("HTTP 429"), "quota"),
        (TransientError("HTTP 503"), "unavailable"),
        (TimeoutError("synthetic-secret"), "unavailable"),
        (ModelOutputError("synthetic-secret"), "invalid_output"),
        (PermanentError("HTTP 400"), "rejected"),
    ],
)
def test_check_failure_categories(api_client, base, monkeypatch, failure, code):
    login(api_client)
    persist(base, zhipu_api_key=SecretStr("synthetic-secret"))

    def fail(_):
        raise failure

    monkeypatch.setattr(api, "build_text_generator", fail)
    result = api_client.post(
        "/api/v1/settings/ai/check", json={"capability": "text", "acknowledge_cost": True}
    )
    assert result.status_code == 200
    assert result.json()["code"] == code
    assert "synthetic-secret" not in result.text


def test_three_capabilities_checked_separately_and_require_consent(api_client, base, monkeypatch):
    login(api_client)
    calls = []

    class Adapter:
        def generate(self, request):
            calls.append("text")
            return "OK"

        def embed(self, text):
            calls.append("embedding")

        def parse(self, content, mime):
            calls.append("vision")
            assert content.startswith(b"\x89PNG")
            return type("Outcome", (), {"fragments": ["synthetic"], "failures": []})()

    monkeypatch.setattr(api, "build_text_generator", lambda _: Adapter())
    monkeypatch.setattr(api, "build_embedder", lambda _: Adapter())
    monkeypatch.setattr(api, "ZhipuVisionParser", lambda *args, **kwargs: Adapter())
    path = "/api/v1/settings/ai/check"
    assert (
        api_client.post(path, json={"capability": "text", "acknowledge_cost": True}).status_code
        == 400
    )
    persist(base, zhipu_api_key=SecretStr("synthetic-secret"))
    assert api_client.post(path, json={"capability": "text"}).status_code == 422
    assert calls == []
    for capability in ("text", "vision", "embedding"):
        response = api_client.post(path, json={"capability": capability, "acknowledge_cost": True})
        assert response.json()["ok"] is True
        assert calls[-1] == capability
    assert len(calls) == 3


@pytest.mark.skipif(sys.platform != "win32", reason="真实 Windows DPAPI 验证")
def test_windows_store_encrypts_synthetic_key(base):
    from pathlib import Path

    base.secret_backend = "dpapi"
    persist(base, zhipu_api_key=SecretStr("synthetic-secret"))
    assert resolve_ai_settings(base).zhipu_api_key == "synthetic-secret"
    encrypted = (Path(base.data_dir) / "ai-settings" / "secrets.dpapi").read_bytes()
    assert b"synthetic-secret" not in encrypted


def test_keyword_question_uses_existing_index_without_calling_embedder(base):
    from test_qa_retrieval import _candidate

    from campusflow.api.deps import DeferredEmbedder
    from campusflow.application.qa import PreparedQuestion, answer_prepared
    from campusflow.domain.ranking import RankingMode
    from campusflow.domain.retrieval import RetrievalScope, ScopedCandidates
    from campusflow.infrastructure.llm.text import FakeTextGenerator

    embedder = DeferredEmbedder(base)
    scope = RetrievalScope()
    prepared = PreparedQuestion(
        workspace_id=1,
        scope=scope,
        question="数据库作业",
        profile_id=embedder.profile.id,
        candidates=ScopedCandidates(
            1, scope, embedder.profile.id, (_candidate("数据库作业说明", 1),), ()
        ),
        unindexed_materials=(),
    )
    generator = FakeTextGenerator(
        '{"insufficient":true,"answer":"","citation_ids":[],"uncertainties":[]}'
    )
    answer = answer_prepared(prepared, embedder, generator, mode=RankingMode.KEYWORD)
    assert answer.insufficient
    assert len(generator.requests) == 1
    with pytest.raises(DomainError, match="未配置"):
        answer_prepared(prepared, embedder, generator, mode=RankingMode.HYBRID)
