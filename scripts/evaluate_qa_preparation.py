"""Q001 离线评测准备：固定回答验证计数流程，不调用检索器、模型或数据库。"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "samples/synthetic/q001/cases.json"
COMPONENT = "q001-offline-preparation-v1"
DISPOSITIONS = {"answer", "uncertain", "conflict", "insufficient"}
STATUSES = {"completed", "failed", "timeout"}


def load_json(path: Path) -> dict:
    def invalid_number(value):
        raise ValueError(f"JSON 数值无效：{value}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 存在重复键")
            result[key] = value
        return result

    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("输入文件超过 2 MiB")
    result = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid_number
    )
    if not isinstance(result, dict):
        raise TypeError("输入必须为 JSON 对象")
    return result


def strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def eligible(document: dict, case: dict) -> bool:
    return document["state"] == "active" and document["scope"] == case["scope"]


def validate_cases(asset: dict) -> None:
    """先验证人工标注完整性，不能把坏标注算成模型错误。"""
    if (
        not isinstance(asset.get("version"), str)
        or not asset.get("corpus")
        or not asset.get("cases")
    ):
        raise ValueError("缺少样本版本、语料或问题")
    if not isinstance(asset["corpus"], list) or not isinstance(asset["cases"], list):
        raise ValueError("语料和问题必须是列表")
    documents = {}
    for doc in asset["corpus"]:
        if not isinstance(doc, dict):
            raise ValueError("语料必须是对象")
        if (
            not isinstance(doc.get("id"), str)
            or not doc["id"]
            or doc["id"] in documents
            or not strings(doc.get("scope"))
            or len(doc["scope"]) != 4
            or doc.get("state") not in {"active", "archived", "unavailable"}
            or not isinstance(doc.get("text"), str)
            or not doc["text"]
            or not isinstance(doc.get("locator"), str)
            or not doc["locator"]
        ):
            raise ValueError("语料标注无效或编号重复")
        documents[doc["id"]] = doc
    seen = set()
    for case in asset["cases"]:
        if not isinstance(case, dict):
            raise ValueError("问题必须是对象")
        if (
            not all(
                isinstance(case.get(k), str) and case[k]
                for k in ("id", "question", "scenario", "input_type")
            )
            or case["id"] in seen
            or not strings(case.get("scope"))
            or len(case["scope"]) != 4
            or type(case.get("top_k")) is not int
            or not 1 <= case["top_k"] <= 20
            or not strings(case.get("relevant_ids"))
            or len(set(case["relevant_ids"])) != len(case["relevant_ids"])
            or not strings(case.get("expected_uncertainties"))
            or len(set(case["expected_uncertainties"])) != len(case["expected_uncertainties"])
            or case.get("expected_disposition") not in DISPOSITIONS
            or not isinstance(case.get("expected_claims"), list)
        ):
            raise ValueError("问题标注无效或编号重复")
        seen.add(case["id"])
        for cid in case["relevant_ids"]:
            if cid not in documents or not eligible(documents[cid], case):
                raise ValueError("预期检索证据失效、缺失或越界")
        claims = set()
        for claim in case["expected_claims"]:
            if not isinstance(claim, dict):
                raise ValueError("事实主张标注必须是对象")
            if (
                not isinstance(claim.get("text"), str)
                or not claim["text"]
                or claim["text"] in claims
                or not strings(claim.get("evidence"))
                or not claim["evidence"]
                or not set(claim["evidence"]) <= set(case["relevant_ids"])
                or not isinstance(claim.get("support_quote"), str)
                or not claim["support_quote"]
                or not all(
                    claim["support_quote"] in documents[cid]["text"] for cid in claim["evidence"]
                )
            ):
                raise ValueError("事实主张缺少有效的人工支持标注")
            claims.add(claim["text"])


def fixture_runs(asset: dict, profile: str) -> dict:
    """只生成已标明为预设的测试回答，禁止称作真实模型基准。"""
    if profile not in {"reference", "degraded", "failure"}:
        raise ValueError("未知预设配置")
    runs = []
    for case in asset["cases"]:
        runs.append(
            {
                "case_id": case["id"],
                "status": "completed",
                "retrieved_ids": case["relevant_ids"][:],
                "claims": [
                    {"text": c["text"], "citations": c["evidence"][:]}
                    for c in case["expected_claims"]
                ],
                "uncertainties": case["expected_uncertainties"][:],
                "disposition": case["expected_disposition"],
            }
        )
    if profile == "degraded":
        by_id = {r["case_id"]: r for r in runs}
        by_id["deadline-a"]["retrieved_ids"] = ["physics-b"]
        by_id["deadline-a"]["claims"][0] = {
            "text": "须在2026年10月12日18:00前提交。",
            "citations": ["physics-b"],
        }
        by_id["deadline-b"]["claims"][0]["citations"] = ["invented-id"]
        by_id["missing-year-time"]["claims"][0]["text"] = "2026年10月15日23:59截止。"
        by_id["missing-year-time"]["uncertainties"] = []
        by_id["conflicting-notices"]["claims"].pop()
        by_id["conflicting-notices"]["uncertainties"] = []
        by_id["partial-coverage"]["uncertainties"] = []
        by_id["archived-only"]["retrieved_ids"] = ["music-archived"]
    if profile == "failure":
        runs[0].update(status="timeout", retrieved_ids=[], claims=[], uncertainties=[])
        runs[1]["claims"] = "invalid-schema"
        runs.pop()  # 缺少运行记录也必须算失败，不能静默跳过。
    return {"mode": "offline_fixture", "config_id": f"fixture-{profile}", "runs": runs}


def valid_run(run: dict) -> bool:
    return (
        isinstance(run, dict)
        and isinstance(run.get("status"), str)
        and run.get("status") in STATUSES
        and isinstance(run.get("disposition"), str)
        and run.get("disposition") in DISPOSITIONS
        and strings(run.get("retrieved_ids"))
        and strings(run.get("uncertainties"))
        and isinstance(run.get("claims"), list)
        and all(
            isinstance(c, dict)
            and isinstance(c.get("text"), str)
            and c["text"].strip()
            and strings(c.get("citations"))
            for c in run["claims"]
        )
    )


def evaluate_case(case: dict, documents: dict, run: dict | None) -> dict:
    malformed = run is not None and not valid_run(run)
    failed = run is None or malformed or run["status"] != "completed"
    status = "failed" if run is None or malformed else run["status"]
    flags = set()
    if failed:
        flags.add("missing_run" if run is None else "invalid_output" if malformed else status)
    usable = not failed
    retrieved = run["retrieved_ids"][: case["top_k"]] if usable else []
    for cid in retrieved:
        if cid not in documents:
            flags.add("unknown_retrieval_id")
        elif not eligible(documents[cid], case):
            flags.add(
                "scope_leak" if documents[cid]["scope"] != case["scope"] else "source_unavailable"
            )
    supported = 0
    matched = set()
    unsupported = []
    seen = set()
    duplicate_claims = 0
    expected = {c["text"]: c for c in case["expected_claims"]}
    claims = run["claims"] if usable else []
    # 相同说法只计一次；若重复说法带不同引用，合并所有引用后核验，不能靠重复提高成绩。
    grouped = {}
    for claim in claims:
        grouped.setdefault(claim["text"], set()).update(claim["citations"])
        if claim["text"] in seen:
            duplicate_claims += 1
        seen.add(claim["text"])
    for claim_text, citations in grouped.items():
        for cid in citations:
            if cid not in documents:
                flags.add("fabricated_citation")
            elif not eligible(documents[cid], case):
                flags.add(
                    "scope_leak"
                    if documents[cid]["scope"] != case["scope"]
                    else "source_unavailable"
                )
            if cid not in retrieved:
                flags.add("citation_not_retrieved")
        gold = expected.get(claim_text)
        if (
            gold
            and citations == set(gold["evidence"])
            and citations <= set(retrieved)
            and all(cid in documents and eligible(documents[cid], case) for cid in citations)
        ):
            supported += 1
            matched.add(claim_text)
        else:
            unsupported.append(claim_text)
    if usable and run["disposition"] != case["expected_disposition"]:
        flags.add("wrong_answer_disposition")
    units = set(run["uncertainties"]) if usable else set()
    expected_units = set(case["expected_uncertainties"])
    # B017：运行失败按应回答主张未获支持计，分母不因失败消失。
    claim_total = len(expected) if failed else len(grouped)
    metrics = {
        "CIT-SUPPORT": [supported, claim_total],
        "UNC-RECALL": [len(units & expected_units), len(expected_units)],
        "UNC-PRECISION": [len(units & expected_units), len(units)],
    }
    return {
        "case_id": case["id"],
        "scenario": case["scenario"],
        "input_type": case["input_type"],
        "status": status,
        "metrics": metrics,
        "hard_failures": sorted(flags),
        "retrieval_diagnostic": {
            "hits": len(set(retrieved) & set(case["relevant_ids"])),
            "expected": len(case["relevant_ids"]),
            "top_k": case["top_k"],
        },
        "answer_coverage_diagnostic": {"hits": len(matched), "expected": len(expected)},
        "unsupported_or_unreviewed_claims": unsupported,
        "missing_claims": sorted(set(expected) - matched),
        "unflagged_uncertainties": sorted(expected_units - units),
        "overflagged_uncertainties": sorted(units - expected_units),
        "duplicate_claim_count": duplicate_claims,
        "model_latency_ms": None,
        "model_cost": None,
        "raw_output": copy.deepcopy(run),
    }


def evaluate(asset: dict, response: dict) -> dict:
    validate_cases(asset)
    if (
        response.get("mode") != "offline_fixture"
        or not isinstance(response.get("config_id"), str)
        or not response["config_id"].startswith("fixture-")
        or not isinstance(response.get("runs"), list)
    ):
        raise ValueError("本脚本只接收 fixture- 前缀的离线预设记录，不接受真实模型成绩")
    case_ids = {c["id"] for c in asset["cases"]}
    runs = {}
    for run in response["runs"]:
        if (
            not isinstance(run, dict)
            or not isinstance(run.get("case_id"), str)
            or run.get("case_id") not in case_ids
            or run["case_id"] in runs
        ):
            raise ValueError("运行记录有未知或重复样本编号")
        runs[run["case_id"]] = run
    documents = {d["id"]: d for d in asset["corpus"]}
    samples = [evaluate_case(c, documents, runs.get(c["id"])) for c in asset["cases"]]
    return {
        "config_id": response["config_id"],
        "mode": "offline_fixture",
        "samples": samples,
    }


def metric_rows(report: dict) -> list[dict]:
    samples = report["samples"]
    groups = [("all", "all", samples)]
    for key in ("scenario", "input_type"):
        groups.extend(
            (key, val, [s for s in samples if s[key] == val])
            for val in sorted({s[key] for s in samples})
        )
    rows = []
    for key, value, group in groups:
        for metric in ("CIT-SUPPORT", "UNC-RECALL", "UNC-PRECISION"):
            numerator = sum(s["metrics"][metric][0] for s in group)
            denominator = sum(s["metrics"][metric][1] for s in group)
            failures = sum(s["status"] != "completed" for s in group)
            rows.append(
                {
                    "config_id": report["config_id"],
                    "metric_id": metric,
                    "slice_key": key,
                    "slice_value": value,
                    "numerator": numerator,
                    "denominator": denominator,
                    "value": numerator / denominator if denominator else "",
                    "unit": "ratio",
                    "run_status": "failed"
                    if failures == len(group)
                    else "partial"
                    if failures
                    else "completed",
                    "failure_count": failures,
                    "timeout_count": sum(s["status"] == "timeout" for s in group),
                    "notes": "离线预设回答；只验证计数，非模型成绩；"
                    + ("not_applicable" if not denominator else ""),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--responses", type=Path, help="可选的离线预设输出 JSON")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="新的输出目录；拒绝覆盖已有结果"
    )
    args = parser.parse_args()
    try:
        asset = load_json(args.cases)
        validate_cases(asset)
        responses = (
            [load_json(args.responses)]
            if args.responses
            else [fixture_runs(asset, profile) for profile in ("reference", "degraded", "failure")]
        )
        reports = [evaluate(asset, r) for r in responses]
        destination = args.output_dir
        if destination.exists():
            raise ValueError("输出目录已存在，请使用新目录以保留历史结果")
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        recorded = datetime.now(UTC).isoformat()
        meta = {
            "recorded_at": recorded,
            "commit_sha": commit,
            "component_version": COMPONENT,
            "sample_set_version": asset["version"],
            "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "responses_sha256": hashlib.sha256(
                json.dumps(responses, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
        }
        destination.mkdir(parents=True)
        evidence = destination / "evidence.json"
        evidence.write_text(
            json.dumps(
                {
                    **meta,
                    "caveat": "仅离线预设自检；未运行检索器或模型，速度、费用及供应商选择未实测。",
                    "reports": reports,
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        with (ROOT / "docs/quality/evaluation-record-template.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            header = next(csv.reader(stream))
        with (destination / "records.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=header)
            writer.writeheader()
            for report in reports:
                for row in metric_rows(report):
                    writer.writerow(
                        {
                            **{
                                k: meta[k]
                                for k in (
                                    "recorded_at",
                                    "commit_sha",
                                    "component_version",
                                    "sample_set_version",
                                )
                            },
                            "run_id": recorded + ":" + report["config_id"],
                            "excluded_count": 0,
                            "evidence_path": str(evidence),
                            "reviewed_by": "",
                            **row,
                        }
                    )
        for report in reports:
            flags = sum(bool(s["hard_failures"]) for s in report["samples"])
            print(
                f"{report['config_id']}: {len(report['samples'])} cases; hard-failure cases={flags}"
            )
        print("离线评测准备完成；真实模型效果、时延、费用和选型仍未验证。")
        return 0
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(f"评测未完成：{error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
