"""N019：通知冲突、跨学期同名与变更接受历史的端到端验收。"""

from test_version_acceptance import accept, make_scope, pair, save, upload


def applicability(client, left, right):
    response = client.get(f"/api/v1/candidates/{left.id}/diff/{right.id}/applicability")
    assert response.status_code == 200, response.text
    return response.json()


def test_conflict_not_acknowledged_keeps_current_requirements(client):
    left, right = pair(
        client,
        old_published="2026-10-02T10:00:00+08:00",
        new_published="2026-10-01T10:00:00+08:00",
    )
    body = applicability(client, left, right)
    assert body["status"] == "conflict"
    assert body["applied"] is False
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "pending"
    blocked = accept(client, left, right, ["due_text"])
    assert blocked.status_code == 400
    assert "conflict" in blocked.json()["detail"]
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "pending"


def test_accepted_change_traces_versions_reason_and_evidence(client):
    left, right = pair(
        client,
        old_published="2026-10-02T10:00:00+08:00",
        new_published="2026-10-01T10:00:00+08:00",
    )
    response = accept(client, left, right, ["due_text"], acknowledge=True)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["baseline_review_status"] == "superseded"
    history = client.get(f"/api/v1/candidates/{left.id}/acceptances").json()
    assert history[0]["baseline_version_id"] != history[0]["comparison_version_id"]
    assert history[0]["reason"] == "按修订通知接受截止时间"
    assert history[0]["evidence_snapshot"][0]["left"]["evidence"][0]["quote"] == "下周五"
    assert history[0]["evidence_snapshot"][0]["right"]["evidence"][0]["quote"] == "下周三"


def test_cross_semester_same_name_is_not_a_duplicate_or_merge_candidate(client):
    first = make_scope(client, "一班")
    second = make_scope(client, "二班")
    target = save(
        client,
        upload(client, "旧通知", "实验报告下周五前提交。", "2026-10-01T10:00:00+08:00"),
        "下周五",
        first,
    )
    other = save(
        client,
        upload(client, "新通知", "实验报告下周五前提交。", "2026-10-02T10:00:00+08:00"),
        "下周五",
        second,
    )
    report = client.get(f"/api/v1/candidates/{target.id}/duplicates").json()
    assert report["suggestions"] == []
    assert report["excluded"][0]["code"] in {"different_semester", "different_course"}
    preview = client.get(
        f"/api/v1/candidates/{target.id}/merge-preview",
        params={"source_ids": [other.id]},
    )
    assert preview.status_code == 400
    assert "不能合并" in preview.json()["detail"]


def test_missing_replacement_declaration_requires_review(client):
    scope = make_scope(client, "一班")
    left = save(
        client,
        upload(client, "旧通知", "实验报告下周五前提交。", "2026-10-01T10:00:00+08:00"),
        "下周五",
        scope,
    )
    right = save(
        client,
        upload(client, "新通知", "实验报告下周三前提交。", "2026-10-02T10:00:00+08:00"),
        "下周三",
        scope,
    )
    body = applicability(client, left, right)
    assert body["status"] == "requires_review"
    assert body["review_required"] is True
    assert "missing_replacement_declaration" in [item["code"] for item in body["reasons"]]
    blocked = accept(client, left, right, ["due_text"])
    assert blocked.status_code == 400
    assert client.get(f"/api/v1/candidates/{left.id}").json()["review_status"] == "pending"
