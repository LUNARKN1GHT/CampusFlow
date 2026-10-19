"""X010：按 B017 口径统计候选提取准确性与不确定信息识别。

纯计算：输入人工标注与系统实际输出，只输出分子、分母和比值。不读写数据库、
不调用模型、不生成样本——这样计数规则本身可以被单独测试和复核，与跑分过程解耦。

计数口径依据 docs/quality/evaluation-metrics.md：
- 分子分母都记原始整数，比值由两者算出，不用比值反推；
- 分母为 0 时比值留空（记 not_applicable），不用 0 或 1 顶替；
- 结构性失败（模型输出不合契约、超时、解析失败）的样本按未命中计入，绝不剔除；
- 结果至少按输入类型与场景分片，另有整体一片。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# 计入"事实提取准确率"的字段。耗时是可修改的规划建议而非原资料事实（X003），
# 因此不参与 EXT-* 的分子分母；它的作用在 R 系列的核对界面上体现。
FACT_FIELDS = ("title", "submission_method", "deliverable", "due_text")

METRIC_IDS = ("EXT-RECALL", "EXT-PRECISION", "UNC-RECALL", "UNC-PRECISION")

# 整体分片名（B017 要求除场景分片外还有一片覆盖全部样本）。
ALL_SLICE = "all"


@dataclass(frozen=True)
class SampleOutcome:
    """一个评测样本：一个人工标注的来源版本，及系统对它的实际输出。"""

    case_id: str
    difficulty: str
    source_key: str
    expected_facts: Mapping[str, str]
    predicted_facts: Mapping[str, str]
    expected_units: frozenset[str]
    system_units: frozenset[str]
    # 结构性失败标记；本模块不据此剔除样本，计数仍按实际输出进行。
    failed: bool = False


@dataclass
class Tally:
    """一个指标的分子与分母，都是原始整数。"""

    numerator: int = 0
    denominator: int = 0

    def record(self, hits: int, total: int) -> None:
        self.numerator += hits
        self.denominator += total


def ratio(numerator: int, denominator: int) -> float | None:
    """B017：分母为 0 时比值留空并记 not_applicable，不写成 0 或 1。"""
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def matched_facts(sample: SampleOutcome) -> int:
    """人工要求提取、且系统给出的取值与之一致的字段数。

    取值不一致算未命中，即使系统确实提取到了该字段——"提了但提错"不是命中。
    """
    return sum(
        1
        for name, value in sample.expected_facts.items()
        if sample.predicted_facts.get(name) == value
    )


def matched_units(sample: SampleOutcome) -> int:
    """人工标注需要核对、系统也确实标出的不确定单元数。"""
    return len(sample.expected_units & sample.system_units)


def fact_counts(samples: Sequence[SampleOutcome]) -> tuple[Tally, Tally]:
    """EXT-RECALL 与 EXT-PRECISION。

    失败样本仍留在召回分母里：它没有产出，人工要求的字段就是没提到，不能因为
    "这次跑挂了"就从分母消失。它不进准确率分母，因为它没有给出任何可判对的提取。
    """
    recall, precision = Tally(), Tally()
    for sample in samples:
        hits = matched_facts(sample)
        recall.record(hits, len(sample.expected_facts))
        precision.record(hits, len(sample.predicted_facts))
    return recall, precision


def uncertainty_counts(samples: Sequence[SampleOutcome]) -> tuple[Tally, Tally]:
    """UNC-RECALL 与 UNC-PRECISION。

    精确率的分母是系统标出的单元数：多标的单元会拉低它，这正是"误报待核对"的
    度量方式，所以失败样本的空白系统输出不人为补分母。
    """
    recall, precision = Tally(), Tally()
    for sample in samples:
        hits = matched_units(sample)
        recall.record(hits, len(sample.expected_units))
        precision.record(hits, len(sample.system_units))
    return recall, precision


@dataclass(frozen=True)
class MetricRow:
    """一条指标记录，对 B017 表格里的一行。"""

    metric_id: str
    slice_key: str
    slice_value: str
    numerator: int
    denominator: int
    value: float | None
    unit: str = "ratio"


def summarize(
    samples: Sequence[SampleOutcome], *, slice_key: str, slice_value: str
) -> list[MetricRow]:
    """把一批样本折成四条指标记录。"""
    counts = dict(
        zip(
            METRIC_IDS,
            (*fact_counts(samples), *uncertainty_counts(samples)),
            strict=True,
        )
    )
    return [
        MetricRow(
            metric_id,
            slice_key,
            slice_value,
            tally.numerator,
            tally.denominator,
            ratio(tally.numerator, tally.denominator),
        )
        for metric_id, tally in counts.items()
    ]


def difficulties(samples: Sequence[SampleOutcome]) -> list[str]:
    """按出现顺序列出样本涉及的难度类别。"""
    return list(dict.fromkeys(sample.difficulty for sample in samples))


def summarize_all(
    samples: Sequence[SampleOutcome], *, slice_key: str = "difficulty"
) -> list[MetricRow]:
    """整体分片 + 每个难度类别一片（B017 要求按场景分片，便于发现类别间的差异）。"""
    rows = summarize(samples, slice_key=slice_key, slice_value=ALL_SLICE)
    for value in difficulties(samples):
        rows.extend(
            summarize(
                [sample for sample in samples if sample.difficulty == value],
                slice_key=slice_key,
                slice_value=value,
            )
        )
    return rows
