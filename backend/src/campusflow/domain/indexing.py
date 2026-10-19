"""Q004 索引进度与覆盖规则；不据部分成功声称完整可检索。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from campusflow.domain.errors import DomainError
from campusflow.domain.states import JobStatus


class BlockIndexState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"  # 没有可索引正文等显式跳过原因，不能计为成功向量。


# 每块最多尝试次数（含第一次）：崩溃或临时失败不会无限重试，也不会无限计费。
MAX_BLOCK_ATTEMPTS = 3

# 块租约默认值：必须长于一次模型调用的等待上限（60 秒），短于作业超时（300 秒）。
DEFAULT_BLOCK_LEASE_SECONDS = 180
MAX_BLOCK_LEASE_SECONDS = 900

# 调用方可以写入的块失败码；只允许安全代码，不能写异常原文或密钥。
BLOCK_FAILURE_CODES = frozenset({"upstream_transient", "invalid_response", "source_unavailable"})

# 由系统在领取/恢复时写入的终态失败码，调用方不能自行指定。
ATTEMPTS_EXHAUSTED_CODE = "attempts_exhausted"
LEASE_EXPIRED_CODE = "lease_expired"
TERMINAL_FAILURE_CODES = frozenset({ATTEMPTS_EXHAUSTED_CODE, LEASE_EXPIRED_CODE})


@dataclass(frozen=True)
class BlockClaim:
    """只在领取事务提交后使用；外部调用结束后凭 token 写回。"""

    block_id: str
    profile_id: str
    token: str
    text: str
    lease_until: datetime


def validated_index_coverage(raw: object, source_count: int) -> tuple[bool, int]:
    """严格验证解析覆盖，损坏/缺失元数据只能算未知，不能把有文本当作完整。"""
    if not isinstance(raw, dict):
        return False, 0
    failures = raw.get("failures")
    if not isinstance(failures, list):
        return False, 0
    known = type(raw.get("fragment_count")) is int and raw["fragment_count"] == source_count
    for failure in failures:
        if not isinstance(failure, dict) or not (
            isinstance(failure.get("locator_type"), str)
            and failure["locator_type"].strip()
            and isinstance(failure.get("locator_value"), str)
            and isinstance(failure.get("reason"), str)
            and failure["reason"].strip()
            and type(failure.get("retryable")) is bool
        ):
            known = False
    return bool(known), len(failures)


@dataclass(frozen=True)
class BlockIndexProgress:
    """单个索引块的进度；只有失败态可以标记可重试，失败码受白名单约束。"""

    block_id: str
    state: BlockIndexState
    retryable: bool = False
    attempts: int = 0
    failure_code: str | None = None

    def __post_init__(self):
        if not isinstance(self.block_id, str) or not self.block_id or len(self.block_id) > 128:
            raise DomainError("索引块编号无效")
        if not isinstance(self.state, BlockIndexState) or type(self.retryable) is not bool:
            raise DomainError("索引块状态无效")
        if self.retryable and self.state != BlockIndexState.FAILED:
            raise DomainError("只有失败块可以标记为可重试")
        if type(self.attempts) is not int or self.attempts < 0:
            raise DomainError("索引块尝试次数无效")
        if self.failure_code is not None and self.failure_code not in (
            BLOCK_FAILURE_CODES | TERMINAL_FAILURE_CODES
        ):
            raise DomainError("索引块失败码无效")


@dataclass(frozen=True)
class IndexProgressSummary:
    """索引汇总；分母始终是完整块清单，fully_searchable 还需覆盖已知且无遗漏。"""

    status: JobStatus
    expected: int
    indexed: int
    pending: int
    running: int
    failed: int
    skipped: int
    retryable_failed: int
    source_coverage_known: bool
    uncovered_source_count: int
    fully_searchable: bool


def _progress_by_id(
    expected_block_ids: Sequence[str], records: Sequence[BlockIndexProgress]
) -> dict[str, BlockIndexProgress]:
    """把进度记录对齐到完整清单；清单外或重复记录直接拒绝，避免旧清单混入计数。"""
    if isinstance(expected_block_ids, (str, bytes)):
        raise DomainError("索引块清单必须是编号序列")
    if any(not isinstance(key, str) or not key or len(key) > 128 for key in expected_block_ids):
        raise DomainError("索引块清单包含无效编号")
    expected = set(expected_block_ids)
    if len(expected) != len(expected_block_ids):
        raise DomainError("索引块清单包含重复编号")
    by_id = {}
    for record in records:
        if not isinstance(record, BlockIndexProgress):
            raise DomainError("索引进度记录无效")
        if record.block_id not in expected:
            raise DomainError("索引进度包含当前清单以外的块")
        if record.block_id in by_id:
            raise DomainError("同一索引块出现多条进度，请先读取数据库中的当前状态")
        by_id[record.block_id] = record
    return by_id


def summarize_index_progress(
    expected_block_ids: Sequence[str],
    records: Sequence[BlockIndexProgress],
    *,
    source_coverage_known: bool = False,
    uncovered_source_count: int = 0,
) -> IndexProgressSummary:
    """按完整块清单计数：缺进度记录算待处理，而非从分母消失。

    调用者须先完成分块快照，再传入其完整清单；known 表示已读取并验证解析覆盖元数据，
    不能仅凭成功片段存在置 True。覆盖未知、失败或跳过均不能声明完整可检索。
    """
    if type(source_coverage_known) is not bool:
        raise DomainError("解析覆盖状态无效")
    if type(uncovered_source_count) is not int or uncovered_source_count < 0:
        raise DomainError("解析未覆盖数量无效")
    by_id = _progress_by_id(expected_block_ids, records)
    counts = dict.fromkeys(BlockIndexState, 0)
    retryable_failed = 0
    for key in expected_block_ids:
        record = by_id.get(key)
        state = record.state if record is not None else BlockIndexState.PENDING
        counts[state] += 1
        retryable_failed += int(record is not None and record.retryable)
    indexed = counts[BlockIndexState.DONE]
    fully_searchable = (
        bool(expected_block_ids)
        and indexed == len(expected_block_ids)
        and source_coverage_known
        and uncovered_source_count == 0
    )
    # 状态取最“未完成”的一档：运行中优先，只有全部待处理才报 PENDING。
    if counts[BlockIndexState.RUNNING]:
        status = JobStatus.RUNNING
    elif counts[BlockIndexState.PENDING]:
        status = (
            JobStatus.PENDING
            if counts[BlockIndexState.PENDING] == len(expected_block_ids)
            else JobStatus.RUNNING
        )
    elif fully_searchable:
        status = JobStatus.DONE
    elif indexed:
        status = JobStatus.PARTIAL
    else:
        status = JobStatus.FAILED
    return IndexProgressSummary(
        status=status,
        expected=len(expected_block_ids),
        indexed=indexed,
        pending=counts[BlockIndexState.PENDING],
        running=counts[BlockIndexState.RUNNING],
        failed=counts[BlockIndexState.FAILED],
        skipped=counts[BlockIndexState.SKIPPED],
        retryable_failed=retryable_failed,
        source_coverage_known=source_coverage_known,
        uncovered_source_count=uncovered_source_count,
        fully_searchable=fully_searchable,
    )


def blocks_needing_work(
    expected_block_ids: Sequence[str], records: Sequence[BlockIndexProgress]
) -> tuple[str, ...]:
    """只返回待处理/临时失败块，跳过已有成功向量；不替代数据库原子领取。

    索引代次、模型配置或来源改变时必须使用新清单/记录集合，不能拿旧结果冒充新结果。
    正在运行的块必须由超时回收流程明确置为失败后才可重试。
    """
    by_id = _progress_by_id(expected_block_ids, records)
    return tuple(
        key
        for key in expected_block_ids
        if key not in by_id
        or by_id[key].state == BlockIndexState.PENDING
        or (by_id[key].state == BlockIndexState.FAILED and by_id[key].retryable)
    )
