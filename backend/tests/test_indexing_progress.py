"""Q004 第一阶段：进度计数、防止部分成功误报完整、成功块不进入重试清单。"""

from dataclasses import replace

import pytest

from campusflow.domain.errors import DomainError
from campusflow.domain.indexing import (
    BlockIndexProgress,
    BlockIndexState,
    blocks_needing_work,
    summarize_index_progress,
)
from campusflow.domain.states import JobStatus


def row(key, state=BlockIndexState.DONE, retryable=False):
    return BlockIndexProgress(key, state, retryable)


def test_all_saved_blocks_and_known_complete_source_are_required():
    complete = summarize_index_progress(
        ["a", "b"], [row("a"), row("b")], source_coverage_known=True
    )
    assert complete.status == JobStatus.DONE and complete.fully_searchable
    assert complete.indexed == complete.expected == 2
    assert not any((complete.pending, complete.running, complete.failed, complete.skipped))


@pytest.mark.parametrize("known,missing", [(False, 0), (True, 1), (False, 3)])
def test_all_vectors_do_not_hide_incomplete_or_unknown_parsing(known, missing):
    summary = summarize_index_progress(
        ["a"],
        [row("a")],
        source_coverage_known=known,
        uncovered_source_count=missing,
    )
    assert summary.status == JobStatus.PARTIAL
    assert not summary.fully_searchable and summary.indexed == 1
    assert summary.uncovered_source_count == missing


@pytest.mark.parametrize("state", [BlockIndexState.FAILED, BlockIndexState.SKIPPED])
def test_failed_and_skipped_blocks_stay_in_denominator(state):
    summary = summarize_index_progress(
        ["a", "b"], [row("a"), row("b", state)], source_coverage_known=True
    )
    assert summary.status == JobStatus.PARTIAL and not summary.fully_searchable
    assert summary.expected == 2 and summary.indexed == 1
    assert summary.failed + summary.skipped == 1


def test_missing_progress_is_pending_not_success():
    summary = summarize_index_progress(["a", "b"], [row("a")], source_coverage_known=True)
    assert summary.status == JobStatus.RUNNING and not summary.fully_searchable
    assert summary.pending == 1
    empty = summarize_index_progress(["a", "b"], [])
    assert empty.status == JobStatus.PENDING and empty.pending == 2


def test_running_work_is_not_terminal_even_when_other_block_failed():
    summary = summarize_index_progress(
        ["a", "b"],
        [
            row("a", BlockIndexState.RUNNING),
            row("b", BlockIndexState.FAILED, True),
        ],
    )
    assert summary.status == JobStatus.RUNNING and summary.running == 1
    assert summary.failed == summary.retryable_failed == 1


@pytest.mark.parametrize(
    "records", [[], [row("a", BlockIndexState.SKIPPED)], [row("a", BlockIndexState.FAILED)]]
)
def test_empty_or_zero_success_index_is_not_complete(records):
    summary = summarize_index_progress(
        [r.block_id for r in records], records, source_coverage_known=True
    )
    assert summary.status == JobStatus.FAILED and not summary.fully_searchable
    assert summary.indexed == 0


def test_retry_does_not_repeat_success_permanent_failure_skipped_or_running():
    keys = ["saved", "temporary", "permanent", "skip", "running", "pending", "missing"]
    records = [
        row("saved"),
        row("temporary", BlockIndexState.FAILED, True),
        row("permanent", BlockIndexState.FAILED),
        row("skip", BlockIndexState.SKIPPED),
        row("running", BlockIndexState.RUNNING),
        row("pending", BlockIndexState.PENDING),
    ]
    assert blocks_needing_work(keys, records) == ("temporary", "pending", "missing")
    recovered = [
        replace(r, state=BlockIndexState.DONE, retryable=False) if r.block_id == "temporary" else r
        for r in records
    ]
    assert blocks_needing_work(keys, recovered) == ("pending", "missing")
    assert records[1].state == BlockIndexState.FAILED  # 不就地篡改历史输入


@pytest.mark.parametrize(
    "keys,records",
    [
        (["a", "a"], []),
        (["a"], [row("b")]),
        (["a"], [row("a"), row("a")]),
        (["a"], [row("a"), row("a", BlockIndexState.FAILED)]),
        ("a", []),
        ([""], []),
        ([False], []),
    ],
)
def test_bad_or_conflicting_snapshots_fail_closed(keys, records):
    with pytest.raises(DomainError):
        summarize_index_progress(keys, records)
    with pytest.raises(DomainError):
        blocks_needing_work(keys, records)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_coverage_known": "true"},
        {"uncovered_source_count": -1},
        {"uncovered_source_count": True},
        {"uncovered_source_count": 1.5},
    ],
)
def test_invalid_coverage_is_not_accepted(kwargs):
    with pytest.raises(DomainError):
        summarize_index_progress(["a"], [row("a")], **kwargs)


@pytest.mark.parametrize(
    "state",
    [
        BlockIndexState.DONE,
        BlockIndexState.PENDING,
        BlockIndexState.RUNNING,
        BlockIndexState.SKIPPED,
    ],
)
def test_only_failed_state_can_carry_retry_permission(state):
    with pytest.raises(DomainError):
        row("a", state, True)


def test_task_progress_or_plain_strings_cannot_be_used_as_index_state():
    with pytest.raises(DomainError):
        row("a", "done")
