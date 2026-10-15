"""Q001 真实运行器的无联网测试：预算、落盘、范围及人工待核对边界。"""

import importlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def live(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("evaluate_qa_live")


def response(endpoint):
    if endpoint == "embeddings":
        return {
            "data": [{"index": 0, "embedding": [1.0] + [0.0] * 1023}],
            "usage": {"prompt_tokens": 10, "total_tokens": 10},
        }
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "disposition": "insufficient",
                            "claims": [],
                            "uncertainties": [
                                {"reason": "insufficient_material", "explanation": "无资料"}
                            ],
                        }
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    }


def test_reservation_saved_before_request_and_budget_stops_second(live, tmp_path):
    path = tmp_path / "billing.json"
    calls = []

    def transport(key, endpoint, payload):
        ledger = json.loads(path.read_text(encoding="utf-8"))
        assert ledger["calls"][-1]["status"] == "started"
        calls.append(endpoint)
        return response(endpoint)

    client = live.Client("private-key", path, live.RESERVE, transport)
    client.embed("embedding-2", "样本", "query")
    with pytest.raises(live.RunStopped, match="budget_limit"):
        client.embed("embedding-3", "样本", "query")
    assert calls == ["embeddings"]
    assert "private-key" not in path.read_text(encoding="utf-8")
    assert client.ledger["calls"][0]["estimated_cny_from_usage"] == "0.0000050"


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": True, "total_tokens": 1},
        {"prompt_tokens": 9000, "total_tokens": 9000},
    ],
)
def test_missing_or_invalid_usage_keeps_reservation_and_stops(live, tmp_path, usage):
    client = live.Client("key", tmp_path / "bill.json", Decimal(1), lambda *args: {"usage": usage})
    with pytest.raises(live.RunStopped):
        client.embed("embedding-2", "样本", "query")
    call = client.ledger["calls"][0]
    assert call["status"] == "failed_unknown_billing"
    assert Decimal(call["reserved_cny"]) == live.RESERVE
    assert call["estimated_cny_from_usage"] is None


@pytest.mark.parametrize("vector", [[0.0] * 1024, [1.0], [float("nan")] * 1024, [True] * 1024])
def test_invalid_vectors_are_not_ranked(live, tmp_path, vector):
    raw = response("embeddings")
    raw["data"][0]["embedding"] = vector
    client = live.Client("key", tmp_path / "bill.json", Decimal(1), lambda *args: raw)
    with pytest.raises(live.RunStopped, match="invalid_"):
        client.embed("embedding-3", "样本", "query")


def test_rank_filters_scope_and_deleted_before_similarity(live):
    asset = live.prep.load_json(live.prep.DEFAULT_CASES)
    case = asset["cases"][0]
    # 错误范围没有向量，若先算相似度再过滤会直接报错。
    sources = live.rank(asset["corpus"], {"physics-a": [1.0, 0.0]}, [1.0, 0.0], case)
    assert [s["id"] for s in sources] == ["physics-a"]


def test_answer_payload_does_not_include_gold_labels(live):
    payload = live.answer_payload(
        "glm-4.5-flash",
        "何时提交",
        [
            {
                "id": "a",
                "text": "原文",
                "locator": "p1",
                "scope": ["w", "s", "c", "a"],
                "expected_claims": "NEVER_SEND_GOLD",
            }
        ],
    )
    text = json.dumps(payload)
    assert "NEVER_SEND_GOLD" not in text and "expected_claims" not in text
    assert payload["thinking"] == {"type": "disabled"}
    assert "tools" not in payload


@pytest.mark.parametrize("bad", ["truncated", "duplicate", "nonfinite", "bad-disposition"])
def test_invalid_answers_retained_without_claiming_success(live, bad):
    raw = response("chat/completions")
    choice = raw["choices"][0]
    if bad == "truncated":
        choice["finish_reason"] = "length"
    elif bad == "duplicate":
        choice["message"]["content"] = '{"claims":[],"claims":[]}'
    elif bad == "nonfinite":
        choice["message"]["content"] = '{"x":NaN}'
    else:
        choice["message"]["content"] = '{"disposition":[],"claims":[],"uncertainties":[]}'
    text, answer = live.parse_answer(raw)
    assert text and answer is None


def test_four_combinations_keep_every_sample_pending_and_no_gold_in_calls(live, tmp_path):
    asset = live.prep.load_json(live.prep.DEFAULT_CASES)
    calls = []

    def transport(key, endpoint, payload):
        calls.append(payload)
        assert "expected_claims" not in json.dumps(payload)
        return response(endpoint)

    client = live.Client("key", tmp_path / "bill.json", Decimal(1), transport)
    report = live.execute(asset, client, tmp_path, repeats=1)
    assert report["status"] == "completed_pending_review"
    assert len(report["runs"]) == 36 and report["unattempted_samples"] == 0
    assert all(r["human_review"] == "pending" and r["reviewer"] == "" for r in report["runs"])
    assert len({r["config_id"] for r in report["runs"]}) == 4
    assert all("physics-foreign" not in json.dumps(c) for c in calls)


def test_network_stop_saves_completed_work_without_retry(live, tmp_path):
    asset = live.prep.load_json(live.prep.DEFAULT_CASES)
    count = 0

    def transport(*args):
        nonlocal count
        count += 1
        raise live.RunStopped("network_failure_or_timeout")

    client = live.Client("key", tmp_path / "bill.json", Decimal(1), transport)
    report = live.execute(asset, client, tmp_path, repeats=2)
    assert count == 1
    assert report["status"] == "stopped" and report["unattempted_samples"] == 72
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert saved == report


def test_default_cli_is_offline(live):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/evaluate_qa_live.py")],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0 and b"No model calls" in result.stdout


def test_transient_windows_file_lock_only_retries_local_replace(live, tmp_path, monkeypatch):
    original = Path.replace
    calls = []

    def replace(source, target):
        calls.append(target)
        if len(calls) == 1:
            raise PermissionError("temporary file lock")
        return original(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    live.save(tmp_path / "result.json", {"status": "saved"})
    assert len(calls) == 2
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "saved"}
