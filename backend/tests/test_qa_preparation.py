"""Q001：离线评测工具会识别错误、保留失败分母，不把预设输出当模型成绩。"""

import copy
import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/evaluate_qa_preparation.py"
SPEC = importlib.util.spec_from_file_location("qa_preparation", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


@pytest.fixture
def asset():
    return QA.load_json(QA.DEFAULT_CASES)


def row(report, metric):
    return next(
        r for r in QA.metric_rows(report) if r["metric_id"] == metric and r["slice_key"] == "all"
    )


def test_reference_only_validates_labelled_fixture_contract(asset):
    report = QA.evaluate(asset, QA.fixture_runs(asset, "reference"))
    assert len(report["samples"]) == 9
    assert all(not s["hard_failures"] for s in report["samples"])
    for metric in ("CIT-SUPPORT", "UNC-RECALL", "UNC-PRECISION"):
        result = row(report, metric)
        assert result["numerator"] == result["denominator"] > 0
    assert all(s["model_latency_ms"] is None and s["model_cost"] is None for s in report["samples"])


def test_degraded_outputs_reduce_scores_and_keep_diagnostics(asset):
    report = QA.evaluate(asset, QA.fixture_runs(asset, "degraded"))
    assert row(report, "CIT-SUPPORT")["value"] < 1
    assert row(report, "UNC-RECALL")["value"] < 1
    samples = {s["case_id"]: s for s in report["samples"]}
    assert "scope_leak" in samples["deadline-a"]["hard_failures"]
    assert "fabricated_citation" in samples["deadline-b"]["hard_failures"]
    assert "source_unavailable" in samples["archived-only"]["hard_failures"]
    assert samples["deadline-a"]["retrieval_diagnostic"]["hits"] == 0
    assert samples["conflicting-notices"]["answer_coverage_diagnostic"] == {
        "hits": 1,
        "expected": 2,
    }


def test_timeout_invalid_and_absent_runs_remain_in_denominators(asset):
    report = QA.evaluate(asset, QA.fixture_runs(asset, "failure"))
    reference = QA.evaluate(asset, QA.fixture_runs(asset, "reference"))
    assert len(report["samples"]) == len(reference["samples"])
    for metric in ("CIT-SUPPORT", "UNC-RECALL"):
        assert row(report, metric)["denominator"] == row(reference, metric)["denominator"]
        assert row(report, metric)["numerator"] < row(reference, metric)["numerator"]
    assert row(report, "CIT-SUPPORT")["failure_count"] == 3
    assert row(report, "CIT-SUPPORT")["timeout_count"] == 1
    assert row(report, "CIT-SUPPORT")["run_status"] == "partial"


@pytest.mark.parametrize(
    ("cid", "flag"),
    [
        ("physics-b", "scope_leak"),
        ("physics-old", "scope_leak"),
        ("physics-foreign", "scope_leak"),
        ("physics-deleted", "source_unavailable"),
        ("made-up", "fabricated_citation"),
    ],
)
def test_correct_text_with_invalid_citation_is_not_supported(asset, cid, flag):
    response = QA.fixture_runs(asset, "reference")
    response["runs"][0]["retrieved_ids"] = [cid]
    response["runs"][0]["claims"][0]["citations"] = [cid]
    result = QA.evaluate(asset, response)["samples"][0]
    assert result["metrics"]["CIT-SUPPORT"] == [0, 1]
    assert flag in result["hard_failures"]


def test_known_citation_outside_returned_top_k_is_not_supported(asset):
    response = QA.fixture_runs(asset, "reference")
    response["runs"][0]["retrieved_ids"] = ["physics-b"] * 3 + ["physics-a"]
    result = QA.evaluate(asset, response)["samples"][0]
    assert result["metrics"]["CIT-SUPPORT"] == [0, 1]
    assert "citation_not_retrieved" in result["hard_failures"]


def test_duplicates_never_inflate_score_or_hide_bad_citations(asset):
    response = QA.fixture_runs(asset, "reference")
    claim = response["runs"][0]["claims"][0]
    response["runs"][0]["claims"].extend([copy.deepcopy(claim)] * 4)
    result = QA.evaluate(asset, response)["samples"][0]
    assert result["metrics"]["CIT-SUPPORT"] == [1, 1]
    assert result["duplicate_claim_count"] == 4
    response["runs"][0]["claims"].append({"text": claim["text"], "citations": ["fake"]})
    assert QA.evaluate(asset, response)["samples"][0]["metrics"]["CIT-SUPPORT"] == [0, 1]


def test_new_wording_requires_manual_review_even_if_citation_exists(asset):
    response = QA.fixture_runs(asset, "reference")
    response["runs"][0]["claims"][0]["text"] = "正确性尚未经人工标注的新说法"
    result = QA.evaluate(asset, response)["samples"][0]
    assert result["metrics"]["CIT-SUPPORT"] == [0, 1]
    assert result["unsupported_or_unreviewed_claims"] == ["正确性尚未经人工标注的新说法"]


def test_empty_answer_cannot_look_like_perfect_score(asset):
    response = QA.fixture_runs(asset, "reference")
    response["runs"][0]["claims"] = []
    response["runs"][0]["disposition"] = "insufficient"
    result = QA.evaluate(asset, response)["samples"][0]
    assert result["metrics"]["CIT-SUPPORT"] == [0, 0]
    assert result["answer_coverage_diagnostic"] == {"hits": 0, "expected": 1}
    assert "wrong_answer_disposition" in result["hard_failures"]
    report = {"samples": [result], "config_id": "fixture-empty"}
    assert row(report, "CIT-SUPPORT")["value"] == ""
    assert "not_applicable" in row(report, "CIT-SUPPORT")["notes"]


def test_extra_uncertainty_lowers_precision_and_duplicates_are_one_unit(asset):
    response = QA.fixture_runs(asset, "reference")
    response["runs"][0]["uncertainties"] = ["invented", "invented"]
    result = QA.evaluate(asset, response)["samples"][0]
    assert result["metrics"]["UNC-PRECISION"] == [0, 1]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("status", []),
        ("disposition", {}),
        ("retrieved_ids", None),
        ("claims", [{"text": "x", "citations": 12}]),
        ("uncertainties", "missing"),
    ],
)
def test_structural_errors_are_recorded_as_failed_samples(asset, key, value):
    response = QA.fixture_runs(asset, "reference")
    response["runs"][0][key] = value
    result = QA.evaluate(asset, response)["samples"][0]
    assert result["status"] == "failed" and result["metrics"]["CIT-SUPPORT"] == [0, 1]


@pytest.mark.parametrize("change", ["duplicate", "unknown", "real-label"])
def test_invalid_run_set_or_real_model_label_is_rejected(asset, change):
    response = QA.fixture_runs(asset, "reference")
    if change == "duplicate":
        response["runs"].append(response["runs"][0])
    elif change == "unknown":
        response["runs"][0]["case_id"] = "not-in-samples"
    else:
        response["config_id"] = "real-model"
    with pytest.raises(ValueError):
        QA.evaluate(asset, response)


@pytest.mark.parametrize(
    "change", ["duplicate-case", "unknown-evidence", "wrong-scope", "bad-quote"]
)
def test_invalid_human_annotations_fail_before_scoring(asset, change):
    if change == "duplicate-case":
        asset["cases"].append(asset["cases"][0])
    elif change == "unknown-evidence":
        asset["cases"][0]["relevant_ids"] = ["missing"]
    elif change == "wrong-scope":
        asset["cases"][0]["relevant_ids"] = ["physics-foreign"]
    else:
        asset["cases"][0]["expected_claims"][0]["support_quote"] = "原文不存在"
    with pytest.raises(ValueError):
        QA.validate_cases(asset)


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', "[]"])
def test_noncanonical_json_rejected(tmp_path, text):
    path = tmp_path / "input.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        QA.load_json(path)


def test_cli_artifacts_use_b017_header_and_refuse_overwrite(tmp_path):
    destination = tmp_path / "result"
    command = [sys.executable, str(SCRIPT), "--output-dir", str(destination)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    evidence = json.loads((destination / "evidence.json").read_text(encoding="utf-8"))
    assert len(evidence["reports"]) == 3 and "预设" in evidence["caveat"]
    assert all(
        len(evidence[key]) == 64 for key in ("cases_sha256", "script_sha256", "responses_sha256")
    )
    with (destination / "records.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {r["metric_id"] for r in rows} == {"CIT-SUPPORT", "UNC-RECALL", "UNC-PRECISION"}
    assert all(r["reviewed_by"] == "" and r["excluded_count"] == "0" for r in rows)
    original = (destination / "evidence.json").read_bytes()
    assert subprocess.run(command, cwd=ROOT, capture_output=True, check=False).returncode == 2
    assert (destination / "evidence.json").read_bytes() == original
