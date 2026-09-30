"""Validate the machine-readable MVP acceptance-case definition."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "docs" / "quality" / "mvp-acceptance-cases.json"
REGISTRY_PATH = ROOT / "samples" / "registry.csv"
EXPECTED_SCENARIOS = {
    "MVP-01": "从资料回答作业 DDL",
    "MVP-02": "截图日期不完整",
    "MVP-03": "用户询问未覆盖课程",
    "MVP-04": "生成未来两周计划",
    "MVP-05": "工作量超出可用时间",
    "MVP-06": "修改学习时间块",
    "MVP-07": "文件部分识别失败",
    "MVP-08": "删除被引用资料",
}
REQUIRED_ARRAYS = (
    "preconditions",
    "steps",
    "expected_results",
    "failure_conditions",
    "evidence_to_capture",
)


def validate() -> list[str]:
    errors: list[str] = []
    definition = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    with REGISTRY_PATH.open(encoding="utf-8", newline="") as stream:
        registered_samples = {row["sample_id"] for row in csv.DictReader(stream)}

    cases = definition.get("cases", [])
    case_ids = [case.get("case_id") for case in cases]
    if len(cases) != 8:
        errors.append(f"expected 8 cases, found {len(cases)}")
    if len(case_ids) != len(set(case_ids)):
        errors.append("case_id values must be unique")
    if set(case_ids) != set(EXPECTED_SCENARIOS):
        errors.append("case IDs must be exactly MVP-01 through MVP-08")

    for case in cases:
        case_id = case.get("case_id", "<missing>")
        if case.get("readme_scenario") != EXPECTED_SCENARIOS.get(case_id):
            errors.append(f"{case_id}: README scenario is missing or changed")
        for field in REQUIRED_ARRAYS:
            value = case.get(field)
            if not isinstance(value, list) or not value:
                errors.append(f"{case_id}: {field} must be a non-empty array")
        for sample_id in case.get("sample_ids", []):
            if sample_id not in registered_samples:
                errors.append(f"{case_id}: unregistered sample {sample_id}")
        for evidence_path in case.get("evidence_to_capture", []):
            prefix = f"evidence/mvp/<run-id>/{case_id}/"
            if not evidence_path.startswith(prefix):
                errors.append(f"{case_id}: evidence path must start with {prefix}")

    return errors


if __name__ == "__main__":
    validation_errors = validate()
    if validation_errors:
        print("\n".join(validation_errors), file=sys.stderr)
        raise SystemExit(1)
    print("8 MVP acceptance cases are complete and valid")
