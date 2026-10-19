"""N010 真实 PostgreSQL/API 验证：导入顺序、范围、引用、并发及删除。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from campusflow.application.materials import delete_material
from campusflow.application.version_relations import record_relation
from campusflow.domain.errors import DomainError
from campusflow.domain.version_relations import VersionRelationKind, compare_scope, relation_key
from campusflow.infrastructure.db.models import (
    Material,
    MaterialVersion,
    MaterialVersionRelation,
    Workspace,
)
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.version_relations import SqlVersionRelations


def add_material(client, title, **metadata):
    response = client.post(
        "/api/v1/materials/text",
        json={
            "title": title,
            "content": f"合成通知：{title}。",
            **metadata,
        },
    )
    assert response.status_code == 201, response.text
    material = response.json()["material"]
    versions = client.get(f"/api/v1/materials/{material['id']}/versions").json()
    return material, versions[0]


def link(client, source, target, kind="replacement_claim", statement="新版声明替代旧版通知"):
    return client.post(
        f"/api/v1/material-versions/{source['id']}/relations",
        json={
            "target_version_id": target["id"],
            "kind": kind,
            "statement": statement,
        },
    )


def relations(client, version):
    response = client.get(f"/api/v1/material-versions/{version['id']}/relations")
    assert response.status_code == 200, response.text
    return response.json()


def scope(client, suffix="A"):
    semester = client.post(
        "/api/v1/semesters",
        json={
            "name": f"合成学期{suffix}",
            "start_date": "2026-09-01",
            "end_date": "2027-01-31",
        },
    ).json()
    course = client.post(
        "/api/v1/courses",
        json={
            "semester_id": semester["id"],
            "name": f"合成课程{suffix}",
        },
    ).json()
    return {"semester_id": semester["id"], "course_id": course["id"], "class_name": "一班"}


@pytest.mark.parametrize(
    "new_first", [True, False], ids=["new-imported-first", "old-imported-first"]
)
def test_import_order_never_changes_declared_direction_or_originals(client, new_first):
    common = scope(client)
    saved = {}
    for name in ["新", "旧"] if new_first else ["旧", "新"]:
        saved[name] = add_material(
            client,
            name,
            publisher="教务处",
            **common,
            published_at=f"2026-10-{'15' if name == '新' else '01'}T12:00:00+08:00",
        )
    new_m, new_v = saved["新"]
    old_m, old_v = saved["旧"]
    original = client.get(f"/api/v1/materials/{old_m['id']}/versions/1/chunks").json()
    response = link(client, new_v, old_v)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["source_version_id"] == new_v["id"]
    assert result["target_version_id"] == old_v["id"]
    assert result["current_scope_comparison"] == "same"
    assert result["origin"] == "user" and result["review_required"]
    assert result["replacement_effective"] is False
    assert result["source_snapshot"]["published_at"] != result["target_snapshot"]["published_at"]
    assert result["source_snapshot"]["publisher"] == "教务处"
    assert relations(client, old_v) == relations(client, new_v)
    assert client.get(f"/api/v1/materials/{old_m['id']}/versions/1/chunks").json() == original
    for material, version in saved.values():
        assert client.get(f"/api/v1/materials/{material['id']}/versions").json() == [version]


@pytest.mark.parametrize(
    "difference",
    ["semester", "class", "course", "missing"],
    ids=["different-semester", "different-class", "different-course", "unknown-scope"],
)
def test_different_or_missing_scope_is_never_direct_replacement(client, difference):
    first_scope = scope(client)
    second_scope = dict(first_scope)
    if difference == "semester":
        second_scope = scope(client, "B")
    elif difference == "class":
        second_scope["class_name"] = "二班"
    elif difference == "course":
        other = client.post(
            "/api/v1/courses",
            json={
                "semester_id": first_scope["semester_id"],
                "name": "另一门课",
            },
        ).json()
        second_scope["course_id"] = other["id"]
    else:
        second_scope["class_name"] = None
    _, first = add_material(client, "版本一", **first_scope)
    _, second = add_material(client, "版本二", **second_scope)
    result = link(client, first, second).json()
    assert result["current_scope_comparison"] == (
        "unknown" if difference == "missing" else "different"
    )
    assert result["replacement_effective"] is False and result["review_required"]


def test_unknown_publication_not_filled_from_import_time_and_date_text_preserved(client):
    _, first = add_material(client, "无发布时间")
    _, second = add_material(client, "仅日期原文")
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session, session.begin():
        session.get(MaterialVersion, second["id"]).source_metadata = {
            "published_text": "10月15日",
            "published_at": None,
        }
    engine.dispose()
    result = link(client, first, second).json()
    assert result["source_snapshot"]["published_at"] is None
    assert result["target_snapshot"]["published_at"] is None
    assert result["target_snapshot"]["version_publication"] == {
        "published_text": "10月15日",
        "published_at": None,
    }
    assert "imported_at" not in result["source_snapshot"]


def test_scope_revision_keeps_original_snapshots_and_marks_changed(client):
    common = scope(client)
    first_m, first = add_material(client, "原版", publisher="原发布人", **common)
    _, second = add_material(client, "新版", **common)
    before = link(client, first, second).json()
    response = client.patch(
        f"/api/v1/materials/{first_m['id']}",
        json={
            "class_name": "二班",
            "publisher": "修正发布人",
        },
    )
    assert response.status_code == 200
    current = relations(client, first)[0]
    assert current["source_snapshot"] == before["source_snapshot"]
    assert current["metadata_changed"] and current["current_scope_comparison"] == "different"
    assert not current["replacement_effective"]


def test_same_source_is_symmetric_idempotent_and_does_not_overwrite_statement(client):
    _, first = add_material(client, "甲")
    _, second = add_material(client, "乙")
    result = link(client, first, second, "same_source", "同一课程通知").json()
    duplicate = link(client, second, first, "same_source", "同一课程通知")
    assert duplicate.status_code == 200 and duplicate.json()["id"] == result["id"]
    changed = link(client, first, second, "same_source", "另一个未经核对的说明")
    assert changed.status_code == 400
    assert len(relations(client, first)) == 1
    assert relations(client, first)[0]["statement"] == "同一课程通知"


def test_distinct_versions_of_same_material_can_be_linked_without_renumbering(client):
    material, first = add_material(client, "同份资料")
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session, session.begin():
        row = MaterialVersion(material_id=material["id"], version_no=2, note="第二版")
        session.add(row)
        session.flush()
        second = {"id": row.id}
    engine.dispose()
    response = link(client, second, first)
    assert response.status_code == 201
    assert response.json()["replacement_effective"] is False
    versions = client.get(f"/api/v1/materials/{material['id']}/versions").json()
    assert [v["version_no"] for v in versions] == [1, 2]


@pytest.mark.parametrize("invalid", ["self", "missing", "blank", "kind", "spoof-actor"])
def test_invalid_request_is_rejected_without_rows(client, invalid):
    _, first = add_material(client, "甲")
    _, second = add_material(client, "乙")
    payload = {
        "target_version_id": second["id"],
        "kind": "replacement_claim",
        "statement": "替代说明",
    }
    if invalid == "self":
        payload["target_version_id"] = first["id"]
    elif invalid == "missing":
        payload["target_version_id"] = 999999
    elif invalid == "blank":
        payload["statement"] = "   "
    elif invalid == "kind":
        payload["kind"] = "accepted"
    else:
        payload["actor"] = "别人"
    response = client.post(f"/api/v1/material-versions/{first['id']}/relations", json=payload)
    assert response.status_code in {400, 404, 422}
    assert relations(client, first) == []


def test_foreign_workspace_cannot_be_linked_or_read(client):
    _, local = add_material(client, "本人的通知")
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session, session.begin():
        foreign = Workspace(name="他人空间")
        session.add(foreign)
        session.flush()
        material = Material(workspace_id=foreign.id, title="他人私密通知", source_type="text")
        session.add(material)
        session.flush()
        version = MaterialVersion(material_id=material.id, version_no=1)
        session.add(version)
        session.flush()
        other = {"id": version.id}
    engine.dispose()
    assert link(client, local, other).status_code == 404
    assert link(client, other, local).status_code == 404
    assert client.get(f"/api/v1/material-versions/{other['id']}/relations").status_code == 404
    assert relations(client, local) == []


def test_archive_blocks_creation_and_is_visible_for_existing_relation(client):
    material, first = add_material(client, "甲")
    _, second = add_material(client, "乙")
    assert link(client, first, second).status_code == 201
    response = client.post(f"/api/v1/materials/{material['id']}/archive")
    assert response.status_code == 200, response.text
    assert relations(client, first)[0]["source_archived"]
    assert link(client, first, second, "same_source").status_code == 400


def test_new_relation_invalidates_delete_confirmation_and_cascade_keeps_other_file(client):
    material, first = add_material(client, "将删除")
    other_material, second = add_material(client, "保留另一份")
    url = f"/api/v1/materials/{material['id']}"
    original = client.get(url + "/delete-impact").json()
    record = link(client, first, second).json()
    assert (
        client.post(url + "/delete", json={"confirm_token": original["confirm_token"]}).status_code
        == 400
    )
    current = client.get(url + "/delete-impact").json()
    assert current["version_relation_ids"] == [record["id"]]
    assert (
        client.post(url + "/delete", json={"confirm_token": current["confirm_token"]}).status_code
        == 204
    )
    assert relations(client, second) == []
    assert (
        client.get(f"/api/v1/materials/{other_material['id']}/versions/1/chunks").status_code == 200
    )


def test_concurrent_repeated_requests_produce_one_record(client):
    _, first = add_material(client, "甲")
    _, second = add_material(client, "乙")
    workspace = client.get("/api/v1/settings").json()["workspace_id"]
    engine = create_engine(TEST_DATABASE_URL)
    barrier = Barrier(2)

    def create(_):
        with Session(engine) as session:
            barrier.wait(timeout=5)
            return record_relation(
                build_repositories(session),
                SqlVersionRelations(session),
                workspace,
                source_version_id=first["id"],
                target_version_id=second["id"],
                kind=VersionRelationKind.REPLACEMENT_CLAIM,
                statement="合成替代声明",
                actor="测试用户",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0].relation.id == results[1][0].relation.id
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MaterialVersionRelation)) == 1
    engine.dispose()


def test_new_routes_require_login(client):
    _, first = add_material(client, "甲")
    _, second = add_material(client, "乙")
    anonymous = TestClient(client.app)
    try:
        assert (
            anonymous.get(f"/api/v1/material-versions/{first['id']}/relations").status_code == 401
        )
        assert link(anonymous, first, second).status_code == 401
    finally:
        anonymous.close()


def test_relation_snapshots_not_exposed_if_other_source_moves_workspace(client):
    _, first = add_material(client, "本地")
    other_material, second = add_material(client, "被移出的资料")
    assert link(client, first, second).status_code == 201
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session, session.begin():
        foreign = Workspace(name="另一个空间")
        session.add(foreign)
        session.flush()
        session.get(Material, other_material["id"]).workspace_id = foreign.id
    engine.dispose()
    assert relations(client, first) == []


def test_relation_creation_racing_deletion_cannot_bypass_confirmation(client):
    material, first = add_material(client, "将删除")
    _, second = add_material(client, "保留")
    token = client.get(f"/api/v1/materials/{material['id']}/delete-impact").json()["confirm_token"]
    workspace = client.get("/api/v1/settings").json()["workspace_id"]
    engine = create_engine(TEST_DATABASE_URL)
    barrier = Barrier(2)

    def create():
        with Session(engine) as session:
            barrier.wait(timeout=5)
            try:
                record_relation(
                    build_repositories(session),
                    SqlVersionRelations(session),
                    workspace,
                    source_version_id=first["id"],
                    target_version_id=second["id"],
                    kind=VersionRelationKind.SAME_SOURCE,
                    statement="同源",
                    actor="测试用户",
                )
                return "linked"
            except DomainError:
                session.rollback()
                return "missing"

    def remove():
        with Session(engine) as session:
            barrier.wait(timeout=5)
            try:
                delete_material(
                    build_repositories(session),
                    client.app.state.file_storage,
                    workspace,
                    material["id"],
                    confirm_token=token,
                    keep_tasks=True,
                )
                return "deleted"
            except DomainError:
                session.rollback()
                return "confirmation_changed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(create), pool.submit(remove)
        outcome = one.result(timeout=15), two.result(timeout=15)
    assert outcome in {("linked", "confirmation_changed"), ("missing", "deleted")}
    engine.dispose()


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ({}, {}, "unknown"),
        ({"semester_id": 1}, {"semester_id": 2}, "different"),
        (
            {"semester_id": 1, "course_id": 2, "class_name": "一班"},
            {"semester_id": 1, "course_id": 2, "class_name": "一班"},
            "same",
        ),
    ],
    ids=["unknown-is-not-wildcard", "known-difference", "same-explicit-scope"],
)
def test_scope_comparison(left, right, expected):
    assert compare_scope(left, right) == expected


def test_relation_direction_and_identity_validation():
    assert relation_key(2, 1, VersionRelationKind.SAME_SOURCE) == (1, 2)
    assert relation_key(2, 1, VersionRelationKind.REPLACEMENT_CLAIM) == (2, 1)
    with pytest.raises(DomainError):
        relation_key(True, 2, VersionRelationKind.SAME_SOURCE)
