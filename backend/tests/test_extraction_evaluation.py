"""X010 测试：B017 的计数口径，以及评测样本清单本身的可用性。

验收：
- 遗漏、误提取、漏标待核对、误报待核对都各自反映到对应指标；
- 结构性失败按未命中计入，不被剔除；
- 分母为 0 时留空记 not_applicable，不写成 0% 或 100%。
"""

import json
from pathlib import Path

from campusflow.application.extraction_evaluation import (
    ALL_SLICE,
    FACT_FIELDS,
    SampleOutcome,
    fact_counts,
    ratio,
    summarize,
    summarize_all,
    uncertainty_counts,
)

CASES_PATH = (
    Path(__file__).resolve().parents[2] / "samples" / "evaluation" / "extraction-cases.json"
)
ASSET = json.loads(CASES_PATH.read_text(encoding="utf-8"))
REASON_UNITS = ASSET["reason_units"]
UNITS = set(REASON_UNITS.values())
DIFFICULTIES = {"partial-coverage", "relative-date", "missing-field", "cross-source"}


def _sample(**overrides) -> SampleOutcome:
    base = {
        "case_id": "case-1",
        "difficulty": "partial-coverage",
        "source_key": "来源版本 #1",
        "expected_facts": {"title": "A", "due_text": "10月15日"},
        "predicted_facts": {"title": "A", "due_text": "10月15日"},
        "expected_units": frozenset({"deadline.time"}),
        "system_units": frozenset({"deadline.time"}),
    }
    base.update(overrides)
    return SampleOutcome(**base)


def _row(rows, metric_id, slice_value=ALL_SLICE):
    return next(r for r in rows if r.metric_id == metric_id and r.slice_value == slice_value)


# --------------------------------------------------------------- 计数口径


def test_value_is_derived_from_integer_counts() -> None:
    """分子分母是原始整数，value 由两者算出，不用比值反推。"""
    samples = [
        _sample(),
        _sample(
            case_id="case-2",
            source_key="来源版本 #2",
            difficulty="relative-date",
            expected_facts={"title": "A"},
            predicted_facts={"title": "A", "due_text": "10月16日"},
        ),
    ]
    row = next(
        r
        for r in summarize(samples, slice_key="difficulty", slice_value=ALL_SLICE)
        if r.metric_id == "EXT-PRECISION"
    )
    # 样本一提取 2 个字段且都对；样本二提取 2 个、其中 due_text 与人工预期不符
    assert (row.numerator, row.denominator) == (3, 4)
    assert row.value == 0.75
    assert isinstance(row.numerator, int) and isinstance(row.denominator, int)
    assert row.unit == "ratio"


def test_wrong_value_is_neither_recall_nor_precision_hit() -> None:
    """提了但提错：召回不算命中，准确率也算错值输出。"""
    sample = _sample(predicted_facts={"title": "A", "due_text": "10月16日"})
    recall, precision = fact_counts([sample])
    assert (recall.numerator, recall.denominator) == (1, 2)
    assert (precision.numerator, precision.denominator) == (1, 2)


def test_missing_field_lowers_recall_only() -> None:
    """漏提：人工要求的字段没提到，只压召回，不压准确率。"""
    sample = _sample(predicted_facts={"title": "A"})
    recall, precision = fact_counts([sample])
    assert (recall.numerator, recall.denominator) == (1, 2)
    assert (precision.numerator, precision.denominator) == (1, 1)


def test_structural_failure_is_counted_not_excluded() -> None:
    """结构失败按未命中计入：仍留在召回分母，不被剔除，也不冒充成功样本。"""
    sample = _sample(predicted_facts={}, system_units=frozenset(), failed=True)
    recall, precision = fact_counts([sample])
    assert (recall.numerator, recall.denominator) == (0, 2)
    assert (precision.numerator, precision.denominator) == (0, 0)
    unc_recall, unc_precision = uncertainty_counts([sample])
    assert (unc_recall.numerator, unc_recall.denominator) == (0, 1)
    assert (unc_precision.numerator, unc_precision.denominator) == (0, 0)


def test_zero_denominator_is_not_applicable() -> None:
    """分母为 0 时 value 留空，不写成 0% 或 100%。"""
    empty = _sample(
        expected_facts={},
        predicted_facts={},
        expected_units=frozenset(),
        system_units=frozenset(),
    )
    assert ratio(0, 0) is None
    rows = summarize([empty], slice_key="difficulty", slice_value=ALL_SLICE)
    assert [(r.denominator, r.value) for r in rows] == [(0, None)] * 4


def test_unflagged_and_overflagged_units_both_move_uncertainty() -> None:
    """漏标压召回，误报压准确率；两者都只影响各自的分子或分母。"""
    unflagged = _sample(system_units=frozenset())
    unc_recall, unc_precision = uncertainty_counts([unflagged])
    assert (unc_recall.numerator, unc_recall.denominator) == (0, 1)
    assert (unc_precision.numerator, unc_precision.denominator) == (0, 0)
    overflagged = _sample(system_units=frozenset({"deadline.time", "source.coverage"}))
    recall, precision = uncertainty_counts([overflagged])
    assert (recall.numerator, recall.denominator) == (1, 1)
    assert (precision.numerator, precision.denominator) == (1, 2)


def test_slices_cover_all_and_each_difficulty() -> None:
    """结果至少按场景分片，另有一片覆盖全部样本。"""
    samples = [
        _sample(),
        _sample(case_id="case-2", source_key="来源版本 #2", difficulty="cross-source"),
    ]
    rows = summarize_all(samples)
    assert {(r.slice_key, r.slice_value) for r in rows} == {
        ("difficulty", ALL_SLICE),
        ("difficulty", "partial-coverage"),
        ("difficulty", "cross-source"),
    }
    assert _row(rows, "EXT-RECALL").denominator == 4
    assert _row(rows, "EXT-RECALL", "cross-source").denominator == 2


def test_estimated_minutes_is_not_a_fact_field() -> None:
    """耗时是可修改的规划建议而非原文事实，不进事实提取准确率。"""
    assert set(FACT_FIELDS) == {"title", "submission_method", "deliverable", "due_text"}
    assert "estimated_minutes" not in FACT_FIELDS


# --------------------------------------------------------------- 样本清单


def test_asset_units_are_defined_in_reason_map() -> None:
    """人工标注的每个不确定单元都必须能由某个核对原因产生，否则无法被系统命中。"""
    for case in ASSET["cases"]:
        for source in case["sources"]:
            unknown = set(source.get("expected_uncertain", ())) - UNITS
            assert not unknown, (case["case_id"], source["source_key"], unknown)


def test_asset_model_facts_quote_the_source_verbatim() -> None:
    """假模型的回答必须逐字出现在片段里，否则应用层证据校验会拒绝它。"""
    for case in ASSET["cases"]:
        for source in case["sources"]:
            for name, value in source["model_facts"].items():
                assert value in source["chunk"], (case["case_id"], name)


def test_asset_baseline_expectation_does_not_exceed_model_facts() -> None:
    """基准配置的前提是假模型按标注作答，人工预期不得超出它给出的内容。"""
    for case in ASSET["cases"]:
        for source in case["sources"]:
            assert set(source["expected_facts"]) <= set(source["model_facts"])
            assert set(source["expected_facts"]) <= set(FACT_FIELDS)


def test_asset_covers_all_four_difficulty_classes() -> None:
    """四类困难样本都要有实际样本，不能只写在文档里。"""
    assert {case["difficulty"] for case in ASSET["cases"]} == DIFFICULTIES


def test_asset_case_declares_sample_ids_matching_sources() -> None:
    for case in ASSET["cases"]:
        assert case["sample_ids"] and len(case["sample_ids"]) == len(case["sources"])
