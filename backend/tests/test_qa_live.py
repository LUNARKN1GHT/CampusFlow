"""Q001 真实运行器的无联网测试：预算、落盘、范围及人工待核对边界。"""

import argparse
import importlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KEYS = {"zhipu": "key"}


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


def over_reservation_response():
    """复审复现的形态：DeepSeek 返回用量远超本次预留，旧代码仍记 completed。"""
    raw = response("chat/completions")
    raw["usage"] = {"prompt_tokens": 20000, "completion_tokens": 10, "total_tokens": 20010}
    return raw


def test_reservation_saved_before_request_and_budget_stops_second(live, tmp_path):
    path = tmp_path / "billing.json"
    calls = []

    def transport(key, endpoint, payload):
        ledger = json.loads(path.read_text(encoding="utf-8"))
        assert ledger["calls"][-1]["status"] == "started"
        calls.append(endpoint)
        return response(endpoint)

    client = live.Client({"zhipu": "private-key"}, path, live.RESERVE, transport)
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
    client = live.Client(KEYS, tmp_path / "bill.json", Decimal(1), lambda *args: {"usage": usage})
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
    client = live.Client(KEYS, tmp_path / "bill.json", Decimal(1), lambda *args: raw)
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
    supplied = json.loads(payload["messages"][1]["content"])
    assert supplied["allowed_citation_ids"] == ["a"]
    assert supplied["sources"][0]["locator"] == "p1"


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

    client = live.Client(KEYS, tmp_path / "bill.json", Decimal(1), transport)
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

    client = live.Client(KEYS, tmp_path / "bill.json", Decimal(1), transport)
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
    first = live.Client(KEYS, billing, Decimal(1), first_transport)
    stopped = live.execute(asset, first, tmp_path, repeats=1)
    assert [r["status"] for r in stopped["runs"]] == ["completed", "failed"]
    old_calls = json.loads(billing.read_text(encoding="utf-8"))["calls"]
    resumed_calls = []

    def second_transport(key, endpoint, payload):
        resumed_calls.append(payload)
        return response(endpoint)

    client = live.Client(KEYS, billing, Decimal(1), second_transport, resume=True)
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
    client = live.Client(KEYS, path, live.RESERVE, lambda *args: response("embeddings"))
    client.embed("embedding-2", "样本", "query")
    resumed = live.Client(
        KEYS, path, live.RESERVE, lambda *args: pytest.fail("no new call"), resume=True
    )
    with pytest.raises(live.RunStopped, match="budget_limit"):
        resumed.embed("embedding-3", "样本", "query")
    with pytest.raises(live.RunStopped, match="billing_already_exists"):
        live.Client(KEYS, path, Decimal(1))
    with pytest.raises(live.RunStopped, match="budget_changed"):
        live.Client(KEYS, path, Decimal(1), resume=True)


def test_completed_resume_makes_no_more_api_calls(live, tmp_path):
    asset = live.prep.load_json(live.prep.DEFAULT_CASES)
    path = tmp_path / "bill.json"
    client = live.Client(KEYS, path, Decimal(1), lambda key, ep, payload: response(ep))
    live.execute(asset, client, tmp_path, repeats=1)
    client = live.Client(
        KEYS, path, Decimal(1), lambda *args: pytest.fail("no new call"), resume=True
    )
    report = live.execute(asset, client, tmp_path, repeats=1, resume=True)
    assert len(report["runs"]) == 36


def test_followup_inherits_budget_once_and_prevents_old_run_restart(live, tmp_path):
    source = tmp_path / "previous"
    target = tmp_path / "next"
    source.mkdir()
    target.mkdir()
    manifest = {
        "cases_sha256": "test",
        "budget_cny": "1",
        "dimensions": 1024,
        "repeats": 2,
        "embedding_cny_per_million_tokens": "0.5",
        "generation_cny_per_million_tokens": "0",
    }
    live.save(source / "manifest.json", manifest)
    live.save(source / "results.json", {"status": "completed_pending_review"})
    original = live.Client(
        KEYS, source / "billing.json", Decimal(1), lambda *args: response("embeddings")
    )
    original.embed("embedding-2", "样本", "index")
    live.prepare_followup(source, target, manifest)
    carried = live.Client(
        KEYS,
        target / "billing.json",
        Decimal(1),
        lambda *args: response("embeddings"),
        resume=True,
    )
    assert carried.ledger == original.ledger
    carried.embed("embedding-3", "样本", "index")
    assert len(carried.ledger["calls"]) == 2
    assert sum(Decimal(c["reserved_cny"]) for c in carried.ledger["calls"]) == 2 * live.RESERVE
    with pytest.raises(live.RunStopped, match="already_forwarded"):
        live.prepare_followup(source, target, manifest)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/evaluate_qa_live.py"),
            "--execute",
            "--resume",
            "--output-dir",
            str(source),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert not (source / ".active").exists()


def test_followup_rejects_changed_budget_and_active_parent(live, tmp_path):
    source = tmp_path / "previous"
    target = tmp_path / "next"
    source.mkdir()
    target.mkdir()
    manifest = {"cases_sha256": "test", "budget_cny": "1"}
    live.save(source / "manifest.json", manifest)
    with pytest.raises(live.RunStopped, match="configuration_changed"):
        live.prepare_followup(source, target, {**manifest, "budget_cny": "2"})
    assert not (target / "billing.json").exists()
    (source / ".active").write_text("running")
    with pytest.raises(live.RunStopped, match="parent_run_active"):
        live.prepare_followup(source, target, manifest)


@pytest.mark.parametrize(
    ("selected", "code", "message"),
    [
        (["missing-year-time", "partial-coverage"], 0, b"2 cases x 2 repeats"),
        (["missing-year-time", "missing-year-time"], 2, None),
        (["unknown"], 2, None),
    ],
)
def test_case_subset_is_explicit_and_validated_without_network(live, selected, code, message):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/evaluate_qa_live.py"),
            "--case-ids",
            *selected,
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == code
    if message:
        assert message in result.stdout and b"No model calls" in result.stdout


def run_cli(*extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/evaluate_qa_live.py"), *extra],
        capture_output=True,
        check=False,
    )


def test_alternate_cases_file_is_used_and_default_is_untouched(live):
    alternate = ROOT / "samples/synthetic/q001/cases-v2.json"
    result = run_cli("--cases", str(alternate))
    assert result.stdout.count(b"cases x 2 repeats") == 1
    assert b"15 cases x 2 repeats" in result.stdout
    assert run_cli().stdout.count(b"9 cases x 2 repeats") == 1


def test_rejected_alternate_cases_file_fails_before_any_output(live):
    missing = ROOT / "samples/synthetic/q001/does-not-exist.json"
    result = run_cli("--cases", str(missing))
    assert result.returncode != 0 and b"No model calls" not in result.stdout


@pytest.mark.parametrize(("budget", "ok"), [("3", True), ("0", False), ("abc", False)])
def test_budget_must_be_a_positive_number(live, budget, ok):
    result = run_cli("--budget-cny", budget)
    assert (result.returncode == 0) is ok
    if ok:
        assert b"budget <= 3 CNY" in result.stdout and b"No model calls" in result.stdout


def test_default_combination_count_stays_pinned(live):
    # 不传新参数时必须仍是历史的两组向量 × 两组回答，否则既有证据不可比。
    result = run_cli()
    assert result.returncode == 0
    assert b"4 combinations; 9 cases x 2 repeats" in result.stdout


def test_planned_three_group_invocation_parses_offline(live):
    # 与本轮真实调用完全一致的参数组合，先在无联网下确认组数、题数与预算。
    result = run_cli(
        "--cases",
        str(ROOT / "samples/synthetic/q001/cases-v2.json"),
        "--budget-cny",
        "3",
        "--embedders",
        "embedding-3",
        "--generators",
        "glm-4-flash-250414",
        "glm-4.5-flash",
        "deepseek-flash",
    )
    assert result.returncode == 0
    assert b"3 combinations; 15 cases x 2 repeats; budget <= 3 CNY" in result.stdout
    assert b"No model calls" in result.stdout


def test_unregistered_or_repeated_models_are_rejected_before_output(live, tmp_path):
    out = tmp_path / "run"
    assert run_cli("--generators", "gpt-4", "--output-dir", str(out)).returncode == 2
    assert not out.exists()
    # 同一组合被重复指定会重复计费，必须拒绝。
    assert run_cli("--embedders", "embedding-3", "embedding-3").returncode == 2
    assert run_cli("--generators", "deepseek-flash", "deepseek-flash").returncode == 2


def test_reserve_is_paid_only_for_paid_generators(live):
    assert live.reserve_for("glm-4-flash-250414") == 0
    assert live.reserve_for("glm-4.5-flash") == 0
    assert live.reserve_for("embedding-2") == live.RESERVE
    assert live.reserve_for("embedding-3") == live.RESERVE
    assert live.reserve_for("deepseek-flash") == (
        Decimal("2") / 10**6 * 8192 + Decimal("8") / 10**6 * 2048
    )
    with pytest.raises(live.RunStopped, match="unapproved_model_or_endpoint"):
        live.reserve_for("gpt-4")


def test_paid_generation_is_reserved_and_priced_from_usage(live, tmp_path):
    client = live.Client(
        {"deepseek": "key"},
        tmp_path / "bill.json",
        Decimal("1"),
        lambda *a: response("chat/completions"),
    )
    payload = live.answer_payload("deepseek-flash", "问", [])
    client.request("chat/completions", payload, "answer")
    call = client.ledger["calls"][0]
    # 预留额必须对应这次请求的实际大小，并记录所用上界以便复算。
    assert call["input_token_bound"] == live.payload_token_bound(payload)
    assert Decimal(call["reserved_cny"]) == live.reserve_for(
        "deepseek-flash", call["input_token_bound"]
    )
    assert Decimal(call["estimated_cny_from_usage"]) == (
        Decimal(20) * live.CHAT_PRICE["deepseek-flash"][0]
        + Decimal(5) * live.CHAT_PRICE["deepseek-flash"][1]
    )
    # 正常用量必须落在预留之内，否则刹车就没有意义。
    assert Decimal(call["estimated_cny_from_usage"]) <= Decimal(call["reserved_cny"])


def test_free_generator_keeps_zero_reservation_and_cost(live, tmp_path):
    client = live.Client(
        KEYS, tmp_path / "bill.json", Decimal("1"), lambda *a: response("chat/completions")
    )
    client.request(
        "chat/completions", live.answer_payload("glm-4-flash-250414", "问", []), "answer"
    )
    call = client.ledger["calls"][0]
    assert Decimal(call["reserved_cny"]) == 0
    assert Decimal(call["estimated_cny_from_usage"]) == 0


def test_paid_generation_still_stops_at_budget_limit(live, tmp_path):
    client = live.Client(
        {"deepseek": "key"},
        tmp_path / "bill.json",
        live.reserve_for("deepseek-flash"),
        lambda *a: response("chat/completions"),
    )
    payload = live.answer_payload("deepseek-flash", "问", [])
    client.request("chat/completions", payload, "answer")
    with pytest.raises(live.RunStopped, match="budget_limit"):
        client.request("chat/completions", payload, "answer")


@pytest.mark.parametrize(
    ("model", "expected"),
    [("deepseek-flash", True), ("glm-4.5-flash", True), ("glm-4-flash-250414", False)],
)
def test_thinking_mode_disabled_where_supported(live, model, expected):
    payload = live.answer_payload(model, "问", [])
    assert ("thinking" in payload) is expected
    if expected:
        assert payload["thinking"] == {"type": "disabled"}


def test_endpoint_must_match_model_kind(live, tmp_path):
    client = live.Client(
        KEYS, tmp_path / "bill.json", Decimal("1"), lambda *a: response("embeddings")
    )
    with pytest.raises(live.RunStopped, match="unapproved_model_or_endpoint"):
        client.request("chat/completions", {"model": "embedding-2"}, "mismatch")
    assert client.ledger["calls"] == []


def test_missing_provider_key_stops_before_any_bookkeeping(live, tmp_path):
    client = live.Client(
        {"zhipu": "key"}, tmp_path / "bill.json", Decimal("1"), lambda *a: pytest.fail("no call")
    )
    with pytest.raises(live.RunStopped, match="missing_provider_key"):
        client.request(
            "chat/completions", live.answer_payload("deepseek-flash", "问", []), "answer"
        )
    assert client.ledger["calls"] == []


def test_cli_names_the_missing_key_without_echoing_any_secret(live, tmp_path):
    out = tmp_path / "run"
    env = {k: v for k, v in os.environ.items() if k not in ("ZHIPU_API_KEY", "DEEPSEEK_API_KEY")}
    env["ZHIPU_API_KEY"] = "zhipu-secret-value"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/evaluate_qa_live.py"),
            "--execute",
            "--output-dir",
            str(out),
            "--embedders",
            "embedding-3",
            "--generators",
            "deepseek-flash",
        ],
        capture_output=True,
        check=False,
        env=env,
    )
    assert result.returncode == 2
    assert b"Missing valid DEEPSEEK_API_KEY. No calls made." in result.stdout
    assert b"zhipu-secret-value" not in result.stdout + result.stderr
    # 缺密钥不得留下半成品目录。
    assert not out.exists()


def test_resume_rejects_wrong_reservation_and_accepts_historical_shape(live, tmp_path):
    path = tmp_path / "bill.json"
    wrong = {
        "sequence": 1,
        "model_requested": "deepseek-flash",
        "purpose": "answer",
        "status": "completed",
        "reserved_cny": "0",
        "estimated_cny_from_usage": None,
        "usage": None,
    }
    live.save(path, {"budget_cny": "1", "calls": [wrong]})
    with pytest.raises(live.RunStopped, match="invalid_saved_ledger"):
        live.Client(KEYS, path, Decimal("1"), resume=True)
    # 历史账本形态（向量记 RESERVE、免费生成记 0）仍须通过校验。
    historical = [
        {**wrong, "model_requested": "embedding-2", "reserved_cny": str(live.RESERVE)},
        {**wrong, "sequence": 2, "model_requested": "glm-4-flash-250414", "reserved_cny": "0"},
    ]
    live.save(path, {"budget_cny": "1", "calls": historical})
    client = live.Client(KEYS, path, Decimal("1"), resume=True)
    assert len(client.ledger["calls"]) == 2


def test_manifest_records_models_and_cross_vendor_prices(live):
    args = argparse.Namespace(
        cases=ROOT / "samples/synthetic/q001/cases-v2.json",
        embedders=["embedding-3"],
        generators=["glm-4-flash-250414", "glm-4.5-flash", "deepseek-flash"],
    )
    manifest = live.manifest_for(
        live.prep.load_json(args.cases), args, Decimal("3"), ["zhipu", "deepseek"]
    )
    prices = manifest["provider_prices_cny_per_million_tokens"]
    assert prices["deepseek-flash"] == {"input": "2", "output": "8"}
    assert prices["glm-4-flash-250414"] == {"input": "0", "output": "0"}
    assert prices["embedding-3"] == {"input": "0.5", "output": "0"}
    assert manifest["embedders"] == ["embedding-3"]
    assert len(manifest["generators"]) == 3
    assert manifest["provider_base_urls"]["deepseek"] == "https://api.deepseek.com/"
    assert manifest["budget_cny"] == "3" and manifest["case_ids"]
    # 付费事实必须能在证据里查到，不能只靠沿用智谱口径的旧字段。
    assert "deepseek" in " ".join(manifest["price_sources"])
    assert "付费档" in manifest["price_basis_note"]


def test_three_group_run_pairs_single_embedder_with_each_generator(live, tmp_path):
    asset = live.prep.load_json(live.prep.DEFAULT_CASES)
    # 跨供应商运行必须同时持有两家密钥，缺一家会被 missing_provider_key 拦住。
    client = live.Client(
        {"zhipu": "key", "deepseek": "key"},
        tmp_path / "bill.json",
        Decimal("1"),
        lambda k, ep, p: response(ep),
    )
    report = live.execute(
        asset,
        client,
        tmp_path,
        repeats=1,
        embedders=("embedding-3",),
        generators=("glm-4-flash-250414", "deepseek-flash"),
    )
    assert report["status"] == "completed_pending_review"
    assert report["planned_samples"] == 18 and len(report["runs"]) == 18
    assert {r["config_id"] for r in report["runs"]} == {
        "embedding-3+glm-4-flash-250414",
        "embedding-3+deepseek-flash",
    }


def test_payload_token_bound_tracks_request_size(live):
    empty = live.answer_payload("deepseek-flash", "问", [])
    one = live.answer_payload(
        "deepseek-flash", "问", [{"id": "d", "text": "文" * 4000, "locator": "p", "scope": []}]
    )
    assert live.payload_token_bound(empty) < live.payload_token_bound(one)
    # 上界不得小于真实序列化字节数，否则预留会低估输入。
    assert live.payload_token_bound(one) >= len(live.serialize_request(one))


def test_paid_chat_reservation_follows_payload_size_not_a_fixed_cap(live):
    small = live.answer_payload("deepseek-flash", "问", [])
    large = live.answer_payload(
        "deepseek-flash",
        "问",
        [{"id": "d-1", "text": "文" * 20000, "locator": "p1", "scope": ["a"]}],
    )
    # sources 的 locator/scope/id 不受固定上限约束，预留必须跟着请求体变大。
    assert live.reserve_for("deepseek-flash", live.payload_token_bound(large)) > live.reserve_for(
        "deepseek-flash", live.payload_token_bound(small)
    )
    # 免费档不受输入长度影响。
    assert live.reserve_for("glm-4.5-flash", live.payload_token_bound(large)) == 0


def test_oversized_paid_payload_is_rejected_before_any_call(live, tmp_path):
    client = live.Client(
        {"deepseek": "key"},
        tmp_path / "bill.json",
        Decimal("0.03"),
        lambda *a: pytest.fail("no call"),
    )
    payload = live.answer_payload(
        "deepseek-flash", "问", [{"id": "d", "text": "文" * 20000, "locator": "p", "scope": []}]
    )
    with pytest.raises(live.RunStopped, match="budget_limit"):
        client.request("chat/completions", payload, "answer")
    # 拒绝发生在记账和任何网络调用之前。
    assert client.ledger["calls"] == []


def test_usage_over_reservation_keeps_real_cost_and_stops(live, tmp_path):
    client = live.Client(
        {"deepseek": "key"},
        tmp_path / "bill.json",
        Decimal("3"),
        lambda *a: over_reservation_response(),
    )
    with pytest.raises(live.RunStopped, match="usage_exceeds_reserved_limit"):
        client.request(
            "chat/completions", live.answer_payload("deepseek-flash", "问", []), "answer"
        )
    call = client.ledger["calls"][0]
    assert call["status"] == "usage_exceeds_reserved_limit"
    assert call["error"] == "usage_exceeds_reserved_limit"
    # 真实费用（复审复现值 0.04008 元）必须留下，而不是只累计固定的 reserved_cny。
    assert Decimal(call["estimated_cny_from_usage"]) == Decimal("0.040080")
    assert Decimal(call["estimated_cny_from_usage"]) > Decimal(call["reserved_cny"])
    assert live.committed_cny(client.ledger["calls"]) == Decimal("0.040080")


def test_over_reservation_stops_the_run_without_further_calls(live, tmp_path):
    asset = live.prep.load_json(live.prep.DEFAULT_CASES)
    calls = []

    def transport(key, endpoint, payload):
        calls.append(endpoint)
        return over_reservation_response() if endpoint == "chat/completions" else response(endpoint)

    client = live.Client(
        {"zhipu": "key", "deepseek": "key"}, tmp_path / "bill.json", Decimal("3"), transport
    )
    report = live.execute(
        asset,
        client,
        tmp_path,
        repeats=1,
        embedders=("embedding-3",),
        generators=("deepseek-flash",),
    )
    assert report["status"] == "stopped"
    assert report["stop_reason"] == "usage_exceeds_reserved_limit"
    # 首次超预留后不得再发起对话调用，也不得凭空记出样本结果。
    assert calls.count("chat/completions") == 1
    assert report["runs"] == []


def test_output_over_max_tokens_is_treated_as_an_anomaly(live, tmp_path):
    raw = response("chat/completions")
    raw["usage"] = {"prompt_tokens": 20, "completion_tokens": 3000, "total_tokens": 3020}
    client = live.Client({"deepseek": "key"}, tmp_path / "bill.json", Decimal("3"), lambda *a: raw)
    with pytest.raises(live.RunStopped, match="usage_exceeds_reserved_limit"):
        client.request(
            "chat/completions", live.answer_payload("deepseek-flash", "问", []), "answer"
        )
    assert client.ledger["calls"][0]["status"] == "usage_exceeds_reserved_limit"


def test_resume_rejects_a_tampered_input_bound(live, tmp_path):
    path = tmp_path / "bill.json"
    base = {
        "sequence": 1,
        "model_requested": "deepseek-flash",
        "purpose": "answer",
        "status": "completed",
        "reserved_cny": str(live.reserve_for("deepseek-flash", live.INPUT_TOKEN_CAP)),
        "estimated_cny_from_usage": None,
        "usage": None,
    }
    live.save(path, {"budget_cny": "1", "calls": [{**base, "input_token_bound": 1}]})
    with pytest.raises(live.RunStopped, match="invalid_saved_ledger"):
        live.Client(KEYS, path, Decimal("1"), resume=True)
    # 自洽的新形态账本必须能继续。
    consistent = {
        **base,
        "input_token_bound": 4096,
        "reserved_cny": str(live.reserve_for("deepseek-flash", 4096)),
    }
    live.save(path, {"budget_cny": "1", "calls": [consistent]})
    assert len(live.Client(KEYS, path, Decimal("1"), resume=True).ledger["calls"]) == 1
