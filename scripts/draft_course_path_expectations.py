#!/usr/bin/env python3
"""C001 验收样本预期标注的机器起草脚本。

从 samples/course-paths/real/ 下的真实 PDF 抽取结构化内容，生成
samples/course-paths/expected/ 下的 YAML 起草稿。输出标注
`needs-human-review: true`，必须经人工抽查后方可作为验收依据。

用法：
    python3 scripts/draft_course_path_expectations.py

脚本只读取 samples/course-paths/real/ 中已登记的 PDF，不访问 data/。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_DIR = REPO_ROOT / "samples" / "course-paths" / "real"
EXPECTED_DIR = REPO_ROOT / "samples" / "course-paths" / "expected"

# 培养方案课程行：代码 名称 必修/选修 学时 学分 开课单位 年级 学期
COURSE_ROW = re.compile(
    r"^([A-Za-z0-9＊*]+[\w]*)\s+(.+?)\s+(必修|选修)\s+(\d+(?:/\d+)*)\s+"
    r"([\d.]+)\s+(.+?)\s+(\d)\s*(秋|春|夏)$"
)
# 分组行：组名 要求学分:x 要求门数:y
GROUP_ROW = re.compile(r"^(.+?)要求学分:([无\d.]+)\s*要求门数:([无\d]+)$")

# 预修关系行（切片表）：序号 课程代码 课程名 开课单位 预修课程名 预修代码 [学期] 状态
REL_STATUS = ("范围内", "已停开/更名")
REL_SEMESTER = re.compile(r"^\d{4}年[春秋]季学期$")


def dump_yaml(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(payload, f, allow_unicode=True, sort_keys=False, width=120)
    print(f"  写出 {path.relative_to(REPO_ROOT)}")


def parse_plan_pdf(pdf_path: Path) -> tuple[list[dict], list[dict], list[str]]:
    """解析培养方案 PDF，返回 (课程行, 分组行, 未匹配行)。"""
    reader = PdfReader(str(pdf_path))
    courses: list[dict] = []
    groups: list[dict] = []
    unmatched: list[str] = []
    for page_no, page in enumerate(reader.pages, start=1):
        for raw in (page.extract_text() or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            m = COURSE_ROW.match(line)
            if m:
                courses.append(
                    {
                        "code": m.group(1),
                        "name": m.group(2),
                        "kind": m.group(3),
                        "hours": m.group(4),
                        "credits": float(m.group(5)),
                        "unit": m.group(6),
                        "grade": int(m.group(7)),
                        "semester": m.group(8),
                        "page": page_no,
                    }
                )
                continue
            g = GROUP_ROW.match(line)
            if g:
                groups.append(
                    {
                        "group": g.group(1).strip(),
                        "required_credits": g.group(2),
                        "required_count": g.group(3),
                        "page": page_no,
                    }
                )
                continue
            # 只记录疑似课程行但未匹配成功的，页眉页脚等噪声忽略
            if re.match(r"^[A-Za-z0-9＊*]+\s", line) and (
                "必修" in line or "选修" in line
            ):
                unmatched.append(f"p{page_no}: {line}")
    return courses, groups, unmatched


def parse_relation_pdf(pdf_path: Path) -> tuple[list[dict], list[str]]:
    """解析预修要求切片 PDF，返回 (关系行, 未匹配行)。切片页 1 对应原表第 14 页。"""
    reader = PdfReader(str(pdf_path))
    relations: list[dict] = []
    unmatched: list[str] = []
    for slice_page, page in enumerate(reader.pages, start=1):
        original_page = slice_page + 13  # 切片页 1 = 原表第 14 页
        for raw in (page.extract_text() or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            ok = (
                len(tokens) >= 7
                and tokens[0].isdigit()
                and tokens[-1] in REL_STATUS
            )
            if ok:
                has_semester = REL_SEMESTER.match(tokens[-2]) is not None
                if has_semester and len(tokens) < 8:
                    ok = False
            if not ok:
                if tokens and tokens[0].isdigit():
                    unmatched.append(f"切片p{slice_page}: {line}")
                continue
            if has_semester:
                semester, status = tokens[-2], tokens[-1]
                head = tokens[1:-2]
            else:
                semester, status = None, tokens[-1]
                head = tokens[1:-1]
            # head: 课程代码 课程名 开课单位 预修课程名 预修代码（均无空格时 5 个 token）
            if len(head) != 5:
                unmatched.append(f"切片p{slice_page}: {line}")
                continue
            relations.append(
                {
                    "seq": int(tokens[0]),
                    "course_code": head[0],
                    "course_name": head[1],
                    "unit": head[2],
                    "prereq_name": head[3],
                    "prereq_code": head[4],
                    "prereq_last_semester": semester,
                    "prereq_status": status,
                    "slice_page": slice_page,
                    "original_page": original_page,
                }
            )
    return relations, unmatched


def meta(source: str, scenario: str) -> dict:
    return {
        "_meta": {
            "source": source,
            "scenario": scenario,
            "generated_by": "scripts/draft_course_path_expectations.py",
            "needs_human_review": True,
            "note": "机器起草稿，未经人工抽查不得作为验收依据；page 为 PDF 页码（1 起）。",
        }
    }


def main() -> int:
    print("== 培养方案 ==")
    plans = [
        ("2025计算机科学与技术（辅修）.pdf", "2025计算机辅修.yaml", "辅修方案全量解析"),
        ("2023计算机科学与技术_专业.pdf", "2023计算机_专业.yaml", "版本差异场景-旧版"),
        ("2026计算机科学与技术_专业.pdf", "2026计算机_专业.yaml", "版本差异场景-新版"),
    ]
    total_unmatched = 0
    for filename, out, scenario in plans:
        pdf = REAL_DIR / filename
        courses, groups, unmatched = parse_plan_pdf(pdf)
        total_unmatched += len(unmatched)
        print(f"{filename}: 课程 {len(courses)} 门, 分组 {len(groups)} 组, 疑似漏匹配 {len(unmatched)} 行")
        for u in unmatched:
            print(f"    待核对 {u}")
        payload = meta(f"real/{filename}", scenario)
        payload["groups"] = groups
        payload["courses"] = courses
        if unmatched:
            payload["rows_needing_review"] = unmatched
        dump_yaml(payload, EXPECTED_DIR / out)

    print("== 预修关系切片 ==")
    pdf = REAL_DIR / "预修要求-计算机切片.pdf"
    relations, unmatched = parse_relation_pdf(pdf)
    total_unmatched += len(unmatched)
    stopped = sum(1 for r in relations if r["prereq_status"] == "已停开/更名")
    courses = sorted({r["course_code"] for r in relations})
    print(
        f"预修要求-计算机切片.pdf: 关系 {len(relations)} 条, 涉及课程 {len(courses)} 门, "
        f"其中预修已停开/更名 {stopped} 条, 疑似漏匹配 {len(unmatched)} 行"
    )
    for u in unmatched:
        print(f"    待核对 {u}")
    payload = meta("real/预修要求-计算机切片.pdf", "强制先修关系解析与缺项/停开场景")
    payload["_meta"]["note"] += " original_page 为原表页码。"
    payload["relations"] = relations
    dump_yaml(payload, EXPECTED_DIR / "预修关系-计算机切片.yaml")

    print(f"\n合计疑似漏匹配 {total_unmatched} 行（需人工核对）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
