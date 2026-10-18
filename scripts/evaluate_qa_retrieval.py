"""Q010：运行检索召回与引用支持评估（默认确定性假向量，不联网）。

用法（在仓库根目录）：
    backend/.venv/bin/python scripts/evaluate_qa_retrieval.py
    ZHIPU_API_KEY=... backend/.venv/bin/python scripts/evaluate_qa_retrieval.py --embedder zhipu
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from campusflow.application.qa_evaluation import EvalCase, EvalDocument, evaluate_all  # noqa: E402
from campusflow.core.config import Settings  # noqa: E402
from campusflow.domain.embeddings import EmbeddingProfile  # noqa: E402
from campusflow.workers.embeddings import DemoEmbedder, build_embedder, profile_from_settings  # noqa: E402

CASES = ROOT / "samples" / "evaluation" / "qa-retrieval-cases.json"


def load_cases(path: Path) -> tuple[int, tuple[EvalCase, ...]]:
    data = json.loads(path.read_text(encoding="utf-8"))
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
    return int(data.get("k", 5)), cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", choices=("fake", "zhipu"), default="fake")
    parser.add_argument("--cases", type=Path, default=CASES)
    args = parser.parse_args()

    settings = Settings(_env_file=None)
    if args.embedder == "zhipu":
        embedder = build_embedder(settings)
    else:
        embedder = DemoEmbedder(profile_from_settings(settings))
    k, cases = load_cases(args.cases)
    results = evaluate_all(cases, embedder, k=k)
    payload = {
        "embedder": args.embedder,
        "profile": profile_from_settings(settings).id,
        "k": k,
        "cases": len(cases),
        "modes": {
            mode: {
                "recall_at_k": result.recall,
                "citation_precision_at_k": result.citation_precision,
                "mrr": result.mrr,
                "cross_scope_errors": result.cross_scope_errors,
                "no_hit_cases": result.no_hit_cases,
                "rows": result.case_rows,
            }
            for mode, result in results.items()
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
