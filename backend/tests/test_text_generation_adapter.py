"""X002：模型 HTTP 边界通过替身测试，不使用密钥或产生真实调用费用。"""

import http.client
import io
import json
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from campusflow.application.ports.generation import GenerationRequest
from campusflow.core.config import Settings
from campusflow.domain.errors import ModelOutputError, PermanentError, TransientError
from campusflow.infrastructure.llm.text import (
    API_URL,
    MAX_RESPONSE_BYTES,
    ZhipuTextGenerator,
    _NoRedirect,
)
from campusflow.workers.generation import build_text_generator

REQUEST = GenerationRequest("输出 JSON", '{"source_chunks":[]}')


def envelope(content='{"items":[]}'):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


@pytest.fixture
def adapter(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: opener)
    model = ZhipuTextGenerator("test-secret", model="glm-4-flash-250414")
    return model, opener


def return_bytes(opener, data):
    stream = io.BytesIO(data)
    opener.open.return_value = stream
    return stream


def test_request_uses_chosen_model_fixed_address_and_separate_messages(adapter):
    model, opener = adapter
    stream = return_bytes(opener, json.dumps(envelope()).encode())
    assert model.generate(REQUEST) == '{"items":[]}'
    args, kwargs = opener.open.call_args
    request = args[0]
    assert request.full_url == API_URL
    assert request.get_header("Authorization") == "Bearer test-secret"
    assert kwargs == {"timeout": 60}
    payload = json.loads(request.data)
    assert payload["model"] == "glm-4-flash-250414"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"] == [
        {"role": "system", "content": REQUEST.system_prompt},
        {"role": "user", "content": REQUEST.source_json},
    ]
    assert "tools" not in payload and payload["stream"] is False
    assert payload["max_tokens"] == 4096
    assert opener.open.call_count == 1
    assert stream.closed


@pytest.mark.parametrize("status", [400, 401, 403, 302, 408, 429, 500, 503])
def test_http_errors_are_classified_without_retry_or_sensitive_body(adapter, status):
    model, opener = adapter
    body = io.BytesIO(b"test-secret private material")
    opener.open.side_effect = urllib.error.HTTPError(API_URL, status, "private", {}, body)
    expected = TransientError if status in (408, 429, 500, 503) else PermanentError
    with pytest.raises(expected) as error:
        model.generate(REQUEST)
    assert str(status) in str(error.value)
    assert "test-secret" not in str(error.value) and "private" not in str(error.value)
    if expected is PermanentError:
        # 4xx 是「请求被拒绝」，不是「模型输出无效」。
        assert not isinstance(error.value, ModelOutputError)
    assert opener.open.call_count == 1 and body.closed


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("private"),
        urllib.error.URLError("private"),
        http.client.IncompleteRead(b"private"),
    ],
    ids=["timeout", "connection", "incomplete-read"],
)
def test_transport_failure_does_not_leak_details(adapter, failure):
    model, opener = adapter
    opener.open.side_effect = failure
    with pytest.raises(TransientError) as error:
        model.generate(REQUEST)
    assert "private" not in str(error.value)
    assert opener.open.call_count == 1


@pytest.mark.parametrize(
    "case",
    [
        "json",
        "choices",
        "multiple",
        "truncated",
        "tools",
        "function",
        "empty",
        "utf8",
        "oversize",
    ],
)
def test_incomplete_or_invalid_response_is_rejected(adapter, case):
    model, opener = adapter
    raw = envelope()
    choice = raw["choices"][0]
    if case == "choices":
        raw["choices"] = []
    elif case == "multiple":
        raw["choices"] *= 2
    elif case == "truncated":
        choice["finish_reason"] = "length"
    elif case == "tools":
        choice["message"]["tool_calls"] = [{"name": "send"}]
    elif case == "function":
        choice["message"]["function_call"] = {"name": "send"}
    elif case == "empty":
        choice["message"]["content"] = " "
    data = json.dumps(raw).encode()
    if case == "json":
        data = b"private invalid json"
    elif case == "utf8":
        data = b"\xff"
    elif case == "oversize":
        data = b"x" * (MAX_RESPONSE_BYTES + 1)
    return_bytes(opener, data)
    with pytest.raises(ModelOutputError):
        model.generate(REQUEST)


def test_redirect_is_not_followed():
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(API_URL, headers={"Authorization": "Bearer secret"})
    with pytest.raises(urllib.error.HTTPError):
        opener.error(
            "http", request, io.BytesIO(), 302, "Found", {"location": "https://example.com/leak"}
        )


def test_factory_uses_independent_text_configuration(monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ZHIPU_API_KEY"):
        build_text_generator(Settings(_env_file=None))
    settings = Settings(_env_file=None, ZHIPU_API_KEY="test-secret")
    model = build_text_generator(settings)
    opener = Mock()
    model._opener = opener
    return_bytes(opener, json.dumps(envelope()).encode())
    model.generate(REQUEST)
    payload = json.loads(opener.open.call_args.args[0].data)
    assert payload["model"] == "glm-4-flash-250414"


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": ""},
        {"api_key": "key\nheader"},
        {"model": ""},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": 0},
        {"timeout_seconds": 121},
        {"max_tokens": True},
        {"max_tokens": 8193},
    ],
    ids=["key", "header", "model", "nan", "zero", "timeout", "bool", "tokens"],
)
def test_bad_adapter_configuration_fails_before_network(overrides):
    kwargs = {"api_key": "test", "model": "glm-4-flash-250414", **overrides}
    with pytest.raises(ValueError):
        ZhipuTextGenerator(**kwargs)
