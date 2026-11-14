"""#625 后续：超长规划区间的历史草案可以丢弃，且不影响当前已接受计划。

366 天上限上线前保存的超长草案既不能接受也不能局部重排；丢弃是唯一出口，
因此这里直接用仓储写入超长草案，绕过生成入口的区间校验。
"""

from datetime import UTC, date, datetime

from index_helpers import open_session
from sqlalchemy import func, select

from campusflow.infrastructure.db.models import PlanBlock, PlanDraft, PlanVersion, Workspace
from campusflow.infrastructure.repositories import build_repositories

LONG_START = date(2025, 1, 1)
LONG_END = date(2027, 1, 1)  # 含首尾 731 天，超过 366 天上限


def _workspace_id(client) -> int:
    return client.get("/api/v1/settings").json()["workspace_id"]


def _insert_draft(
    workspace_id: int,
    *,
    range_start: date = LONG_START,
    range_end: date = LONG_END,
    blocks: int = 1,
) -> tuple[int, list[int]]:
    """直接用仓储写入草案、时间块与版本，模拟限制上线前已经存在的数据。"""
    with open_session() as session, session.begin():
        repos = build_repositories(session)
        draft = repos.plans.create_draft(
            workspace_id,
            semester_id=None,
            range_start=range_start,
            range_end=range_end,
            input_snapshot={},
            input_fingerprint="0" * 64,
            assumptions=[],
            gaps=[],
            unscheduled=[],
            conflicts=[],
            coverage={},
        )
        created = repos.plans.create_blocks(
            draft.id,
            [
                {
                    "title": f"历史时间块{index}",
                    "starts_at": datetime(2025, 1, 2, 9, 0, tzinfo=UTC),
                    "ends_at": datetime(2025, 1, 2, 10, 0, tzinfo=UTC),
                    "minutes": 60,
                    "seq": index,
                    "reason": "限制上线前的草案",
                }
                for index in range(blocks)
            ],
        )
        repos.plans.add_version(
            draft.id, version_no=draft.version, change="generated", snapshot={"legacy": True}
        )
        return draft.id, [block.id for block in created]


def _accept_draft(draft_id: int, *, workspace_id: int) -> None:
    """把草案直接改成已接受状态，避免依赖排期链路。"""
    with open_session() as session, session.begin():
        repos = build_repositories(session)
        accepted = repos.plans.bump_version(
            draft_id,
            expected_version=1,
            change="accepted",
            snapshot={"accepted": True},
            status="accepted",
            accepted_at=datetime(2026, 10, 19, 0, 30, tzinfo=UTC),
        )
        assert accepted is not None
        assert accepted.workspace_id == workspace_id


def _count(model, column, value: int) -> int:
    with open_session() as session:
        return session.scalar(select(func.count()).select_from(model).where(column == value))


def test_overlong_draft_can_be_discarded(client) -> None:
    workspace_id = _workspace_id(client)
    draft_id, block_ids = _insert_draft(workspace_id)
    assert block_ids

    listed = client.get("/api/v1/plans/drafts")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [draft_id]

    # 超长草案无法接受，界面必须有一个明确的出口。
    rejected = client.post(f"/api/v1/plans/drafts/{draft_id}/accept", json={"expected_version": 1})
    assert rejected.status_code == 400, rejected.text
    assert "366" in rejected.json()["detail"]
    assert "丢弃" in rejected.json()["detail"]

    discarded = client.delete(f"/api/v1/plans/drafts/{draft_id}")
    assert discarded.status_code == 204, discarded.text

    assert client.get("/api/v1/plans/drafts").json() == []
    with open_session() as session:
        assert session.get(PlanDraft, draft_id) is None
    # 时间块与版本随草案级联清理，不留下孤立记录。
    assert _count(PlanBlock, PlanBlock.draft_id, draft_id) == 0
    assert _count(PlanVersion, PlanVersion.draft_id, draft_id) == 0


def test_overlong_range_rejection_suggests_discard(client) -> None:
    response = client.post(
        "/api/v1/plans/drafts",
        json={"range_type": "custom", "start_date": "0001-01-01", "end_date": "9999-12-31"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "366" in detail
    assert "请新建不超过 366 天的草案" in detail
    assert "旧草案可丢弃后重新生成" in detail
    assert client.get("/api/v1/plans/drafts").json() == []


def test_accepted_draft_cannot_be_discarded(client) -> None:
    workspace_id = _workspace_id(client)
    draft_id, _ = _insert_draft(workspace_id, blocks=0)
    _accept_draft(draft_id, workspace_id=workspace_id)

    response = client.delete(f"/api/v1/plans/drafts/{draft_id}")
    assert response.status_code == 409
    assert response.json()["detail"] == "只有待接受草案可以丢弃"

    current = client.get("/api/v1/plans/current").json()
    assert current is not None
    assert current["id"] == draft_id
    assert _count(PlanDraft, PlanDraft.id, draft_id) == 1


def test_discard_draft_from_another_workspace_is_not_found(client) -> None:
    with open_session() as session, session.begin():
        other = Workspace(name="另一个工作空间", timezone="Asia/Shanghai")
        session.add(other)
        session.flush()
        other_id = other.id
    draft_id, _ = _insert_draft(other_id, blocks=0)

    response = client.delete(f"/api/v1/plans/drafts/{draft_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "计划草案不存在"
    # 跨空间请求不删除任何数据。
    assert _count(PlanDraft, PlanDraft.id, draft_id) == 1


def test_unknown_draft_discard_returns_404(client) -> None:
    response = client.delete("/api/v1/plans/drafts/999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "计划草案不存在"


def test_discarding_draft_keeps_current_accepted_plan(client) -> None:
    workspace_id = _workspace_id(client)
    accepted_id, accepted_blocks = _insert_draft(
        workspace_id, range_start=date(2026, 10, 19), range_end=date(2026, 11, 1)
    )
    assert accepted_blocks
    _accept_draft(accepted_id, workspace_id=workspace_id)
    before = client.get("/api/v1/plans/current").json()
    assert before["id"] == accepted_id
    assert len(before["blocks"]) == len(accepted_blocks)

    draft_id, _ = _insert_draft(
        workspace_id, range_start=date(2026, 10, 19), range_end=date(2026, 10, 20)
    )
    discarded = client.delete(f"/api/v1/plans/drafts/{draft_id}")
    assert discarded.status_code == 204, discarded.text

    after = client.get("/api/v1/plans/current").json()
    assert after["id"] == before["id"]
    assert after["version"] == before["version"]
    assert after["status"] == "accepted"
    assert [block["id"] for block in after["blocks"]] == [block["id"] for block in before["blocks"]]
    assert _count(PlanBlock, PlanBlock.draft_id, accepted_id) == len(accepted_blocks)
