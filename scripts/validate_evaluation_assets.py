"""Validate evaluation metric definitions and the result CSV template."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS_PATH = ROOT / "docs" / "quality" / "evaluation-metrics.json"
RECORD_PATH = ROOT / "docs" / "quality" / "evaluation-record-template.csv"
REQUIRED_METRIC_FIELDS = {
    "metric_id",
    "name",
    "numerator",
    "denominator",
    "aggregation",
    "failure_definition",
    "unit",
    "target",
    "target_status",
}
EXPECTED_RECORD_FIELDS = [
    "run_id",
    "recorded_at",
    "commit_sha",
    "config_id",
    "component_version",
    "sample_set_version",
    "metric_id",
    "slice_key",
    "slice_value",
    "numerator",
    "denominator",
    "value",
    "unit",
    "run_status",
    "failure_count",
    "timeout_count",
    "excluded_count",
    "exclusion_reason",
    "evidence_path",
    "reviewed_by",
    "notes",
]


def validate() -> list[str]:
    errors: list[str] = []
    definition = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    metrics = definition.get("metrics", [])
    metric_ids: set[str] = set()
    for index, metric in enumerate(metrics, start=1):
        missing = REQUIRED_METRIC_FIELDS - metric.keys()
        if missing:
            errors.append(f"metric {index}: missing fields {sorted(missing)}")
            continue
        metric_id = metric["metric_id"]
        if metric_id in metric_ids:
            errors.append(f"duplicate metric_id: {metric_id}")
        metric_ids.add(metric_id)
        for field in (
            "name",
            "numerator",
            "denominator",
            "aggregation",
            "failure_definition",
            "unit",
        ):
            if not isinstance(metric[field], str) or not metric[field].strip():
                errors.append(f"{metric_id}: {field} must be non-empty text")
        if metric["target"] is not None:
            errors.append(
                f"{metric_id}: target must remain null until baseline measurement"
            )
        if metric["target_status"] != "deferred_until_baseline":
            errors.append(f"{metric_id}: target_status must be deferred_until_baseline")

    required_categories = {
        "EXT-RECALL",
        "CIT-SUPPORT",
        "UNC-RECALL",
        "PLAN-CONSTRAINT",
        "EDIT-COST",
    }
    if not required_categories.issubset(metric_ids):
        errors.append(
            "required extraction, citation, uncertainty, planning and edit-cost metrics are missing"
        )

    with RECORD_PATH.open(encoding="utf-8", newline="") as stream:
        record_fields = next(csv.reader(stream))
    if record_fields != EXPECTED_RECORD_FIELDS:
        errors.append(
            "evaluation record CSV header does not match the documented schema"
        )
    return errors


if __name__ == "__main__":
    validation_errors = validate()
    if validation_errors:
        print("\n".join(validation_errors), file=sys.stderr)
        raise SystemExit(1)
    print("evaluation metrics and record template are valid")
