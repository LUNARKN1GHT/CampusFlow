"""Validate the acceptance-sample authorization registry.

This check deliberately uses only the Python standard library so contributors can
run it before the backend environment is installed.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "samples" / "registry.csv"
REQUIRED_FIELDS = (
    "sample_id",
    "title",
    "origin_type",
    "source_description",
    "license_or_permission",
    "allowed_uses",
    "deidentification",
    "repository_path",
    "retention",
    "status",
    "reviewed_by",
    "reviewed_on",
)
SAMPLE_ID = re.compile(r"^CF-[A-Z]+-[0-9]{3}$")
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
ORIGIN_TYPES = {"synthetic", "authorized-private", "public-reference"}
STATUSES = {"active", "withdrawn", "superseded"}


def validate() -> list[str]:
    errors: list[str] = []
    with REGISTRY.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        missing_columns = [
            field for field in REQUIRED_FIELDS if field not in (reader.fieldnames or [])
        ]
        if missing_columns:
            return [f"registry is missing columns: {', '.join(missing_columns)}"]

        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            prefix = f"line {line_number}"
            for field in REQUIRED_FIELDS:
                if not (row.get(field) or "").strip():
                    errors.append(f"{prefix}: {field} is required")

            sample_id = (row.get("sample_id") or "").strip()
            if sample_id and not SAMPLE_ID.fullmatch(sample_id):
                errors.append(f"{prefix}: invalid sample_id {sample_id!r}")
            if sample_id in seen_ids:
                errors.append(f"{prefix}: duplicate sample_id {sample_id!r}")
            seen_ids.add(sample_id)

            origin_type = (row.get("origin_type") or "").strip()
            if origin_type and origin_type not in ORIGIN_TYPES:
                errors.append(f"{prefix}: unsupported origin_type {origin_type!r}")

            status = (row.get("status") or "").strip()
            if status and status not in STATUSES:
                errors.append(f"{prefix}: unsupported status {status!r}")

            reviewed_on = (row.get("reviewed_on") or "").strip()
            if reviewed_on and not ISO_DATE.fullmatch(reviewed_on):
                errors.append(f"{prefix}: reviewed_on must use YYYY-MM-DD")

            repository_path = (row.get("repository_path") or "").strip()
            if repository_path:
                path = Path(repository_path)
                if path.is_absolute() or ".." in path.parts:
                    errors.append(
                        f"{prefix}: repository_path must stay inside the repository"
                    )
                elif repository_path in seen_paths:
                    errors.append(
                        f"{prefix}: duplicate repository_path {repository_path!r}"
                    )
                elif status == "active" and not (ROOT / path).exists():
                    errors.append(
                        f"{prefix}: active sample path does not exist: {repository_path}"
                    )
                seen_paths.add(repository_path)

            if origin_type == "synthetic":
                if (
                    row.get("license_or_permission") or ""
                ).strip() != "project-internal":
                    errors.append(
                        f"{prefix}: synthetic samples must use project-internal permission"
                    )
                if (
                    row.get("deidentification") or ""
                ).strip() != "not-applicable-synthetic":
                    errors.append(
                        f"{prefix}: synthetic samples must declare not-applicable-synthetic"
                    )

    return errors


if __name__ == "__main__":
    validation_errors = validate()
    if validation_errors:
        for error in validation_errors:
            print(error, file=sys.stderr)
        raise SystemExit(1)
    print("sample registry is valid")
