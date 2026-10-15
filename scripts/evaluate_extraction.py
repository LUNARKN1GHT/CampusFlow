"""X010：用假模型跑一遍提取评测，产出 B017 格式的逐项记录。

用途只有一个：把 B017 的计数口径跑通，并说明"解析成功不等于事实提取准确"。
模型回答来自 samples/evaluation/extraction-cases.json 里人工写好的 model_facts，
配置名带 fake 前缀，不冒充真实模型成绩。不连接数据库、不联网：片段在内存里构造，
直接走与线上相同的生成、字段检查、覆盖范围注入和冲突比对代码。

用法（仓库根目录执行）：

    uv run --locked --project backend python scripts/evaluate_extraction.py

产出：
    docs/quality/extraction-evaluation-records.csv   B017 记录表
    docs/evaluation/x010-extraction-evidence.json    逐样本证据（含遗漏与误提取明细）
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from campusflow.application.candidate_generation import (
    InvalidModelOutput,
    generate_candidates,
)
from campusflow.application.extraction_evaluation import (
    FACT_FIELDS,
    SampleOutcome,
    matched_facts,
    summarize_all,
)
from campusflow.application.ports.candidates import CandidateData
from campusflow.application.ports.repositories import MaterialVersionData, SourceChunkData
from campusflow.application.source_uncertainty import (
    conflict_entries_of,
    stored_uncovered_ranges,
    with_coverage_reasons,
)
from campusflow.application.task_extraction import TASK_SPEC, check_task_fields
from campusflow.domain.candidates import CandidateKind, CandidateScope, CandidateDraft
from campusflow.domain.errors import DomainError
from campusflow.domain.source_conflicts import field_conflicts
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.llm.text import FakeTextGenerator

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = REPO_ROOT / "samples" / "evaluation" / "extraction-cases.json"
RECORDS_PATH = REPO_ROOT / "docs" / "quality" / "extraction-evaluation-records.csv"
EVIDENCE_PATH = REPO_ROOT / "docs" / "evaluation" / "x010-extraction-evidence.json"

COMPONENT_VERSION = "campusflow-extraction-pipeline@v1"
CHUNK_ID = 1
WORKSPACE_ID = 1
FROZEN_AT = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

CSV_HEADER = (
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
)

# 每个配置就是一次 run。三次 run 的样本与解析结果完全相同，差别只在模型回答质量，
# 用来证明指标动的是提取准确性，不是解析成功率。
CONFIGS: tuple[dict, ...] = (
    {
        "config_id": "fake-baseline",
        "description": "假模型按人工标注作答：用于验证计数口径本身",
        "inject_coverage": True,
        "malformed": frozenset(),
        "patches": {},
    },
    {
        "config_id": "fake-degraded",
        "description": (
            "同一批样本、同样解析成功，只有模型回答变差：漏提产出物、答错截止原文、"
            "丢掉时刻、忽略未覆盖范围"
        ),
        "inject_coverage": False,
        "malformed": frozenset(),
        "patches": {
            # 漏提：明明读到了产出物，却报成缺失
            ("blurred-notice", "来源版本 #21"): {
                "deliverable": None,
                # 答错并丢掉时刻：只留下日期
                "due_text": "2026 年 10 月 16 日",
            },
            # 误提取：把助教的一句答复当成截止要求
            ("chat-truncated", "来源版本 #11"): {"due_text": "请以课程平台为准"},
        },
    },
    {
        "config_id": "fake-invalid-output",
        "description": (
            "其中一个来源的模型回答不合 JSON 契约：该样本按结构性失败计入失败数，"
            "不剔除、不重跑、不用其余样本补摊"
        ),
        "inject_coverage": True,
        "malformed": frozenset({("conflicting-notices", "来源版本 #62")}),
        "patches": {},
    },
)


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _field_payload(name: str, value: object, chunk_id: int) -> dict:
    """把一个字段写成模型契约要求的 JSON；缺失一律用 null 加缺失说明。"""
    if name == "estimated_minutes":
        # 耗时只能是规划建议（X003），评测里统一按"无法估计"上报
        return {
            "name": name,
            "value": None,
            "origin": "suggestion",
            "missing_reason": "原资料未给出可依据的工作量",
        }
    if value is None:
        return {
            "name": name,
            "value": None,
            "origin": "document",
            "missing_reason": "原资料未明确说明",
        }
    return {
        "name": name,
        "value": value,
        "origin": "document",
        "evidence": [{"chunk_id": chunk_id, "quote": value}],
    }


def _response_body(facts: dict, chunk_id: int) -> str:
    fields = [_field_payload(definition.name, facts.get(definition.name), chunk_id) for definition in TASK_SPEC.fields]
    return json.dumps({"items": [{"kind": "task", "fields": fields}]}, ensure_ascii=False)


def _chunk(index: int, text: str) -> SourceChunkData:
    return SourceChunkData(
        id=CHUNK_ID,
        version_id=index,
        seq=0,
        locator_type="paragraph",
        locator_value="1",
        text=text,
        page=None,
        paragraph=1,
        bbox=None,
        confidence=None,
        table_group=None,
    )


def _version(index: int, coverage: dict | None) -> MaterialVersionData:
    return MaterialVersionData(
        id=index,
        material_id=index,
        version_no=1,
        note=None,
        storage_key=None,
        checksum=None,
        created_at=FROZEN_AT,
        source_metadata={} if coverage is None else {"coverage": coverage},
    )


def _scope(case_index: int, class_name: str | None) -> CandidateScope:
    # 每个 case 用各自的课程号，避免不同难度的样本被误当成同一条事项互相比较；
    # 同一 case 内的多个来源共享课程号，只有它们才该比对取值冲突。
    return CandidateScope(semester_id=1, course_id=case_index, class_name=class_name)


class Produced:
    """一次样本提取的中间结果：草稿可能因为结构性失败而不存在。"""

    def __init__(self, case, source: dict, index: int) -> None:
        self.case_id = case["case_id"]
        self.difficulty = case["difficulty"]
        self.source_key = source["source_key"]
        self.index = index
        self.expected_facts = dict(source["expected_facts"])
        self.expected_units = frozenset(source.get("expected_uncertain", ()))
        self.draft: CandidateDraft | None = None
        self.failed = False

    def candidate(self) -> CandidateData:
        assert self.draft is not None
        return CandidateData(
            id=self.index,
            workspace_id=WORKSPACE_ID,
            source_version_id=self.index,
            draft=self.draft,
            review_status=ReviewStatus.PENDING,
            created_at=FROZEN_AT,
        )


def _produce(case, case_index: int, source: dict, index: int, config: dict, reason_units: dict) -> Produced:
    """走一遍线上链路：生成 → 字段检查 → 覆盖范围注入。"""
    produced = Produced(case, source, index)
    key = (case["case_id"], source["source_key"])
    if key in config["malformed"]:
        # 模拟模型没按契约作答；这里不再尝试修复或重跑
        produced.failed = True
        return produced

    facts = dict(source["model_facts"])
    for name, value in config["patches"].get(key, {}).items():
        if value is None:
            facts.pop(name, None)
        else:
            facts[name] = value

    chunks = [_chunk(index, source["chunk"])]
    scope = _scope(case_index, source.get("class_name"))
    try:
        drafts = generate_candidates(
            FakeTextGenerator(_response_body(facts, CHUNK_ID)), TASK_SPEC, chunks, scope=scope
        )
        checked = [check_task_fields(draft) for draft in drafts]
        if config["inject_coverage"]:
            checked = with_coverage_reasons(
                checked, stored_uncovered_ranges(_version(index, source.get("coverage")))
            )
    except (InvalidModelOutput, DomainError):
        produced.failed = True
        return produced
    produced.draft = checked[0]
    return produced


def _system_units(draft: CandidateDraft | None, reason_units: dict) -> frozenset[str]:
    if draft is None:
        return frozenset()
    return frozenset(
        reason_units[reason.code]
        for reason in draft.review_reasons
        if reason.code in reason_units
    )


def run_config(cases: list[dict], reason_units: dict, config: dict) -> tuple[list[SampleOutcome], list[dict]]:
    """跑完一个配置：每个来源一个样本，最后整体做一次跨来源冲突比对。"""
    produced: list[Produced] = []
    index = 0
    for case_index, case in enumerate(cases, start=1):
        for source in case["sources"]:
            index += 1
            produced.append(_produce(case, case_index, source, index, config, reason_units))

    # 跨来源冲突比对走线上同一段代码：先摊平成条目，再按适用范围分组判定
    extra_units: dict[int, set[str]] = {}
    candidates = [item.candidate() for item in produced if item.draft is not None]
    for conflict in field_conflicts(conflict_entries_of(candidates)):
        for entry in conflict.entries:
            extra_units.setdefault(entry.candidate_id, set()).add(reason_units["source_conflict"])

    samples: list[SampleOutcome] = []
    detail: list[dict] = []
    for item in produced:
        predicted = {}
        if item.draft is not None:
            predicted = {
                field.name: field.value
                for field in item.draft.fields
                if field.name in FACT_FIELDS and field.value is not None
            }
        system_units = _system_units(item.draft, reason_units) | frozenset(
            extra_units.get(item.index, ())
        )
        sample = SampleOutcome(
            case_id=item.case_id,
            difficulty=item.difficulty,
            source_key=item.source_key,
            expected_facts=item.expected_facts,
            predicted_facts=predicted,
            expected_units=item.expected_units,
            system_units=system_units,
            failed=item.failed,
        )
        samples.append(sample)
        detail.append(_sample_detail(sample, predicted))
    return samples, detail


def _sample_detail(sample: SampleOutcome, predicted: dict) -> dict:
    """逐样本记录：命中、遗漏、误提取、漏标待核对、误报待核对。"""
    return {
        "case_id": sample.case_id,
        "difficulty": sample.difficulty,
        "source_key": sample.source_key,
        "structural_failure": sample.failed,
        "expected_facts": dict(sample.expected_facts),
        "predicted_facts": dict(predicted),
        "matched_facts": sorted(
            name for name, value in sample.expected_facts.items() if predicted.get(name) == value
        ),
        "missing_facts": sorted(set(sample.expected_facts) - set(predicted)),
        "wrong_facts": sorted(
            name
            for name, value in sample.expected_facts.items()
            if name in predicted and predicted[name] != value
        ),
        "extra_facts": sorted(set(predicted) - set(sample.expected_facts)),
        "expected_uncertain": sorted(sample.expected_units),
        "system_uncertain": sorted(sample.system_units),
        "unflagged_uncertain": sorted(sample.expected_units - sample.system_units),
        "overflagged_uncertain": sorted(sample.system_units - sample.expected_units),
        "matched_fact_count": matched_facts(sample),
    }


def _run_status(samples: list[SampleOutcome]) -> str:
    failures = sum(1 for sample in samples if sample.failed)
    if failures == 0:
        return "completed"
    return "partial" if failures < len(samples) else "failed"


def _csv_rows(samples: list[SampleOutcome], config: dict, meta: dict, notices: str) -> list[dict]:
    failures = sum(1 for sample in samples if sample.failed)
    run_id = f"x010-{config['config_id']}-{meta['recorded_at'][:10]}"
    notes = f"{config['description']}；模型为假适配器（FakeTextGenerator，固定回答）；{notices}"
    rows = []
    for row in summarize_all(samples):
        # 分母为 0：value 留空并记 not_applicable，不用 0 或 1 顶替
        row_notes = (
            f"分母为 0，not_applicable；{notes}" if row.denominator == 0 else notes
        )
        rows.append(
            {
                "run_id": run_id,
                "recorded_at": meta["recorded_at"],
                "commit_sha": meta["commit_sha"],
                "config_id": config["config_id"],
                "component_version": COMPONENT_VERSION,
                "sample_set_version": meta["sample_set_version"],
                "metric_id": row.metric_id,
                "slice_key": row.slice_key,
                "slice_value": row.slice_value,
                "numerator": row.numerator,
                "denominator": row.denominator,
                "value": "" if row.value is None else f"{row.value:.4f}",
                "unit": row.unit,
                "run_status": _run_status(samples),
                "failure_count": failures,
                "timeout_count": 0,
                "excluded_count": 0,
                "exclusion_reason": "",
                "evidence_path": EVIDENCE_PATH.relative_to(REPO_ROOT).as_posix(),
                "reviewed_by": "",
                "notes": row_notes,
            }
        )
    return rows


def _print_summary(config: dict, samples: list[SampleOutcome]) -> None:
    """控制台只打 ASCII 摘要，中文明细都在产出文件里，避免终端编码干扰。"""
    print(f"\n=== {config['config_id']}  ({_run_status(samples)}, {len(samples)} samples) ===")
    for row in summarize_all(samples):
        value = "n/a" if row.value is None else f"{row.value:.4f}"
        print(
            f"  {row.metric_id:<14} {row.slice_key}={row.slice_value:<18} "
            f"{row.numerator}/{row.denominator} = {value}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="X010 提取与不确定信息识别评测（假模型口径）")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--records", type=Path, default=RECORDS_PATH)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE_PATH)
    args = parser.parse_args(argv)

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = payload["cases"]
    reason_units = payload["reason_units"]

    meta = {
        "recorded_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "commit_sha": _git_commit(),
        "sample_set_version": payload["sample_set_version"],
    }
    notices = (
        "分母为 0 记 not_applicable；结构性失败计入 failure_count 并在召回分母中按未命中计，"
        "不剔除样本；未提供 key 的指标（引用支撑、计划、编辑成本、提醒、时延、费用）不适用本项，"
        "未生成记录行"
    )

    all_rows: list[dict] = []
    evidence_runs: list[dict] = []
    for config in CONFIGS:
        samples, detail = run_config(cases, reason_units, config)
        all_rows.extend(_csv_rows(samples, config, meta, notices))
        evidence_runs.append(
            {
                "config_id": config["config_id"],
                "description": config["description"],
                "model": "fake（FakeTextGenerator，回答取自样本清单的 model_facts）",
                "inject_coverage_reasons": config["inject_coverage"],
                "run_status": _run_status(samples),
                "structural_failure_count": sum(1 for sample in samples if sample.failed),
                "samples": detail,
            }
        )
        _print_summary(config, samples)

    args.records.parent.mkdir(parents=True, exist_ok=True)
    with args.records.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_HEADER))
        writer.writeheader()
        writer.writerows(all_rows)

    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(
            {
                "generated_at": meta["recorded_at"],
                "commit_sha": meta["commit_sha"],
                "sample_set_version": meta["sample_set_version"],
                "component_version": COMPONENT_VERSION,
                "metric_spec": "docs/quality/evaluation-metrics.md",
                "source_asset": "docs/quality/evaluation-record-template.csv",
                "caveat": (
                    "模型回答是事先写好的固定文本（假适配器），本文件只用于核对 B017 计数口径，"
                    "不代表真实模型成绩；解析在本评测中固定为成功，不进入任何指标。"
                ),
                "runs": evidence_runs,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"\nwrote {args.records} ({len(all_rows)} rows)")
    print(f"wrote {args.evidence}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
