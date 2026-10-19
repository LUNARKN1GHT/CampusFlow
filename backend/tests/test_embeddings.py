"""Q002：不联网的协议、版本隔离与可重试错误验证。"""

import io
import json
import traceback
import urllib.error
import urllib.request
from dataclasses import replace
from unittest.mock import Mock

import pytest

from campusflow.application.embeddings import embed_registered_text
from campusflow.core.config import Settings
from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile, cosine_similarity
from campusflow.domain.errors import (
    DomainError,
    ModelOutputError,
    PermanentError,
    TransientError,
)
from campusflow.infrastructure.llm.embeddings import (
    API_URL,
    MAX_RESPONSE_BYTES,
    ZhipuEmbedder,
    _NoRedirect,
)
from campusflow.workers.embeddings import build_embedder

PROFILE = EmbeddingProfile()


def envelope():
    return {"model": "embedding-3", "data": [{"index": 0, "embedding": [0.1] * 1024}]}


@pytest.fixture
def adapter(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: opener)
    return ZhipuEmbedder("test-secret", profile=PROFILE), opener


def respond(opener, raw=None):
    stream = io.BytesIO(json.dumps(envelope() if raw is None else raw).encode())
    opener.open.return_value = stream
    return stream


def test_explicit_call_preserves_input_and_configuration(adapter):
    model, opener = adapter
    opener.open.assert_not_called()
    stream = respond(opener)
    result = model.embed(" 作业通知\n原文 ")
    result.validate_for(PROFILE)
    request = opener.open.call_args.args[0]
    assert request.full_url == API_URL
    assert request.get_header("Authorization") == "Bearer test-secret"
    assert json.loads(request.data) == {
        "model": "embedding-3",
        "input": [" 作业通知\n原文 "],
        "dimensions": 1024,
        "encoding_format": "float",
    }
    assert opener.open.call_args.kwargs == {"timeout": 60}
    assert stream.closed and opener.open.call_count == 1


@pytest.mark.parametrize(
    "change",
    [
        {"model": "embedding-2"},
        {"provider": "another"},
        {"revision": "q001-v2"},
        {"dimensions": 512},
        {"max_input_bytes": 2000},
    ],
)
def test_different_profiles_cannot_be_compared_even_at_same_dimension(change):
    other = replace(PROFILE, **change)
    assert other.id != PROFILE.id
    q = EmbeddedText(PROFILE.id, (0.1,) * 1024, PROFILE.model)
    candidate = EmbeddedText(other.id, (0.1,) * other.dimensions, other.model)
    with pytest.raises(DomainError, match="不一致"):
        cosine_similarity(q, candidate, PROFILE)


def test_identity_is_stable_and_configuration_is_not_mutable_through_dict():
    PROFILE.generation_config["encoding_format"] = "base64"
    assert PROFILE.id == EmbeddingProfile().id
    q = EmbeddedText(PROFILE.id, (1e308,) * 1024, PROFILE.model)
    assert cosine_similarity(q, q, PROFILE) == pytest.approx(1)


@pytest.mark.parametrize("text", ["", "   ", None, "中" * 1001, "\ud800"])
def test_invalid_input_never_reaches_provider(adapter, text):
    model, opener = adapter
    with pytest.raises(DomainError):
        model.embed(text)
    opener.open.assert_not_called()


@pytest.mark.parametrize("status", [302, 400, 401, 403, 408, 429, 500, 503])
def test_error_is_safe_and_temporary_failure_can_be_explicitly_retried(adapter, status):
    model, opener = adapter
    body = io.BytesIO(b"test-secret private-source")
    opener.open.side_effect = urllib.error.HTTPError(
        API_URL, status, "test-secret private-source", {}, body
    )
    error_type = TransientError if status in (408, 429, 500, 503) else PermanentError
    with pytest.raises(error_type) as caught:
        model.embed("private-source")
    rendered = "".join(traceback.format_exception(caught.value))
    # 检查异常消息与其链；源码行本身不包含传入密钥。
    assert "test-secret" not in str(caught.value)
    assert "HTTPError:" not in rendered
    assert caught.value.__suppress_context__ and body.closed
    assert opener.open.call_count == 1
    if error_type is PermanentError:
        # 4xx 是「请求被拒绝」，不是「模型输出无效」。
        assert not isinstance(caught.value, ModelOutputError)
    if error_type is TransientError:
        opener.open.side_effect = None
        respond(opener)
        assert model.embed("private-source").profile_id == PROFILE.id
        assert opener.open.call_count == 2


@pytest.mark.parametrize("error", [TimeoutError("secret"), urllib.error.URLError("secret")])
def test_network_failure_is_retryable_and_does_not_log_sensitive_detail(adapter, error, caplog):
    model, opener = adapter
    opener.open.side_effect = error
    with pytest.raises(TransientError) as caught:
        model.embed("private-source")
    assert "secret" not in str(caught.value)
    assert "secret" not in caplog.text and "private-source" not in caplog.text
    assert opener.open.call_count == 1


@pytest.mark.parametrize(
    "fault",
    [
        "model",
        "missing-model",
        "short",
        "zero",
        "nan",
        "infinity",
        "bool",
        "string",
        "huge-int",
        "index",
        "bool-index",
        "duplicate",
        "empty",
        "not-list",
    ],
)
def test_invalid_provider_vectors_are_rejected(adapter, fault):
    model, opener = adapter
    raw = envelope()
    row = raw["data"][0]
    if fault == "model":
        raw["model"] = "embedding-2"
    elif fault == "missing-model":
        del raw["model"]
    elif fault == "short":
        row["embedding"].pop()
    elif fault == "zero":
        row["embedding"] = [0] * 1024
    elif fault in ("nan", "infinity", "bool", "string", "huge-int"):
        row["embedding"][0] = {
            "nan": float("nan"),
            "infinity": float("inf"),
            "bool": True,
            "string": "0.1",
            "huge-int": 10**400,
        }[fault]
    elif fault in ("index", "bool-index"):
        row["index"] = 1 if fault == "index" else False
    elif fault == "duplicate":
        raw["data"] *= 2
    elif fault == "empty":
        raw["data"] = []
    elif fault == "not-list":
        row["embedding"] = "x" * 1024
    respond(opener, raw)
    with pytest.raises(ModelOutputError):
        model.embed("原文")


@pytest.mark.parametrize(
    "data",
    [b"\xff", b"invalid", b'{"model":1,"model":2}', b"x" * (MAX_RESPONSE_BYTES + 1)],
    ids=["utf8", "json", "duplicate-key", "oversize"],
)
def test_unreadable_or_oversize_response_fails_closed(adapter, data):
    model, opener = adapter
    opener.open.return_value = io.BytesIO(data)
    with pytest.raises(ModelOutputError):
        model.embed("原文")


def test_redirect_never_forwards_authorization():
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(API_URL, headers={"Authorization": "Bearer secret"})
    with pytest.raises(urllib.error.HTTPError):
        opener.error(
            "http", request, io.BytesIO(), 302, "Found", {"location": "https://example.com/leak"}
        )


def test_registered_entry_rejects_unknown_configuration_and_bad_adapter_result():
    registry, embedder = Mock(), Mock()
    embedder.profile = PROFILE
    for stored in (None, replace(PROFILE, revision="next")):
        registry.get.return_value = stored
        with pytest.raises(DomainError):
            embed_registered_text(registry, embedder, PROFILE.id, "原文")
    embedder.embed.assert_not_called()
    registry.get.return_value = PROFILE
    embedder.embed.return_value = EmbeddedText("wrong", (0.1,) * 1024, PROFILE.model)
    with pytest.raises(DomainError):
        embed_registered_text(registry, embedder, PROFILE.id, "原文")
    embedder.embed.return_value = EmbeddedText(PROFILE.id, (0.1,) * 1024, PROFILE.model)
    assert embed_registered_text(registry, embedder, PROFILE.id, "原文").profile_id == PROFILE.id


def test_factory_separates_text_and_embedding_configuration(monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ZHIPU_API_KEY"):
        build_embedder(Settings(_env_file=None))
    model = build_embedder(Settings(_env_file=None, ZHIPU_API_KEY="fake", text_model="other"))
    assert model.profile == PROFILE


@pytest.mark.parametrize(
    "changes",
    [
        {"api_key": ""},
        {"api_key": "test\nsecret"},
        {"api_key": "中文"},
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"timeout_seconds": float("nan")},
        {"profile": replace(PROFILE, dimensions=512)},
        {"profile": replace(PROFILE, model="embedding-2")},
    ],
)
def test_invalid_settings_fail_before_network(changes):
    with pytest.raises(ValueError):
        ZhipuEmbedder(**{"api_key": "test", "profile": PROFILE, **changes})
