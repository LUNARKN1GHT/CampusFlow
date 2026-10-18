"""Q010：评估集与确定性排序的召回、引用支持和跨范围检查。"""

import json
from pathlib import Path

from campusflow.application.qa_evaluation import EvalCase, EvalDocument, evaluate_all
from campusflow.core.config import Settings
from campusflow.workers.embeddings import DemoEmbedder, profile_from_settings

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "samples" / "evaluation" / "qa-retrieval-cases.json"


def load_cases():
    data = json.loads(CASES.read_text(encoding="utf-8"))
    cases = tuple(
        EvalCase(
            id=item["id"],
            question=item["question"],
            semester_id=item.get("semester_id"),
            course_id=item.get("course_id"),
            class_name=item.get("class_name"),
            support=tuple(item.get("support", [])),
            documents=tuple(
                EvalDocument(
                    id=document["id"],
                    text=document["text"],
                    semester_id=document.get("semester_id"),
                    course_id=document.get("course_id"),
                    class_name=document.get("class_name"),
                    state=document.get("state", "active"),
                )
                for document in item["documents"]
            ),
        )
        for item in data["cases"]
    )
    return int(data["k"]), cases


def test_labeled_cases_measure_recall_support_and_scope_errors():
    k, cases = load_cases()
    embedder = DemoEmbedder(profile_from_settings(Settings(_env_file=None)))
    results = evaluate_all(cases, embedder, k=k)

    keyword = results["keyword"]
    assert keyword.recall == 1.0
    assert keyword.cross_scope_errors == []
    assert keyword.no_hit_cases == []
    for row in keyword.case_rows:
        assert all(not title.startswith(("d", "t")) for title in row["ranked"])

    # 假向量不包含课程名语义，课程编号样本必须由关键词基线评估；
    # 这条失败正是真实模型“缺少课程名信息”时的对照证据。
    assert results["vector"].recall < 1.0
    assert "ddl-course-code" in results["vector"].no_hit_cases
    assert results["vector"].cross_scope_errors == []

    for result in results.values():
        for row in result.case_rows:
            assert "d1" not in row["ranked"]
            assert "d2" not in row["ranked"]
            assert "d3" not in row["ranked"]
            assert "t1" not in row["ranked"]
            assert "t2" not in row["ranked"]


def test_evaluation_is_reproducible():
    k, cases = load_cases()
    embedder = DemoEmbedder(profile_from_settings(Settings(_env_file=None)))
    first = evaluate_all(cases, embedder, k=k)
    second = evaluate_all(cases, embedder, k=k)
    for mode in first:
        assert first[mode].recall == second[mode].recall
        assert first[mode].mrr == second[mode].mrr
        assert first[mode].case_rows == second[mode].case_rows
