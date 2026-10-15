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


def test_resume_preserves_failed_attempt_budget_and_skips_completed(live, tmp_path):
    asset = live.prep.load_json(live.prep.DEFAULT_CASES)
    count = 0

    def first_transport(key, endpoint, payload):
        nonlocal count
        count += 1
        if count == 13:
            raise live.RunStopped("network_failure_or_timeout")
        return response(endpoint)

    billing = tmp_path / "bill.json"
    first = live.Client("key", billing, Decimal(1), first_transport)
    stopped = live.execute(asset, first, tmp_path, repeats=1)
    assert [r["status"] for r in stopped["runs"]] == ["completed", "failed"]
    old_calls = json.loads(billing.read_text(encoding="utf-8"))["calls"]
    resumed_calls = []

    def second_transport(key, endpoint, payload):
        resumed_calls.append(payload)
        return response(endpoint)

    client = live.Client("key", billing, Decimal(1), second_transport, resume=True)
    result = live.execute(asset, client, tmp_path, repeats=1, resume=True)
    assert result["status"] == "completed_pending_review"
    assert result["unattempted_samples"] == 0 and len(result["runs"]) == 37
    assert client.ledger["calls"][: len(old_calls)] == old_calls
    assert client.ledger["calls"][13]["sequence"] == 14
    assert result["runs"][1]["status"] == "failed"
    repeats = [
        r
        for r in result["runs"]
        if r["case_id"] == "deadline-b" and r["config_id"] == "embedding-2+glm-4-flash-250414"
    ]
    assert len(repeats) == 2 and repeats[-1]["attempt"] == 2
    # 第一组首题已完成，继续运行不得再把它发送给同一回答模型。
    sent = [
        json.loads(p["messages"][1]["content"])["question"]
        for p in resumed_calls
        if p["model"] == "glm-4-flash-250414"
    ]
    # 第二个检索模型仍会正常测试该题，只有一次。
    assert sent.count(asset["cases"][0]["question"]) == 1


def test_resume_does_not_reset_reserved_budget(live, tmp_path):
    path = tmp_path / "bill.json"
    client = live.Client("key", path, live.RESERVE, lambda *args: response("embeddings"))
    client.embed("embedding-2", "样本", "query")
    resumed = live.Client(
        "key", path, live.RESERVE, lambda *args: pytest.fail("no new call"), resume=True
    )
    with pytest.raises(live.RunStopped, match="budget_limit"):
        resumed.embed("embedding-3", "样本", "query")
    with pytest.raises(live.RunStopped, match="billing_already_exists"):
        live.Client("key", path, Decimal(1))
    with pytest.raises(live.RunStopped, match="budget_changed"):
        live.Client("key", path, Decimal(1), resume=True)


def test_completed_resume_makes_no_more_api_calls(live, tmp_path):
    asset = live.prep.load_json(live.prep.DEFAULT_CASES)
    path = tmp_path / "bill.json"
    client = live.Client("key", path, Decimal(1), lambda key, ep, payload: response(ep))
    live.execute(asset, client, tmp_path, repeats=1)
    client = live.Client(
        "key", path, Decimal(1), lambda *args: pytest.fail("no new call"), resume=True
    )
    report = live.execute(asset, client, tmp_path, repeats=1, resume=True)
    assert len(report["runs"]) == 36
