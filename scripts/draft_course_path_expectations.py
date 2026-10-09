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


# 替代关系切片：切片页码（1 起）→ 原表页码
SUBSTITUTE_PAGES = [2, 3, 10, 11, 12, 13, 23, 24, 25, 26, 28, 29, 33, 34, 46, 47, 48, 49, 62]
SUB_HEAD = re.compile(r"^(\d+) #(\d+)\s+(.+)$")
SUB_CODES = re.compile(r"\d(\.\d+)?\s*学分")
CS_CODE = re.compile(r"^CS\d{4}")


def _is_codes_line(line: str) -> bool:
    # 代码行的特征：含「·」且「学分」前有数字；课程名如「数学分析(A1)」虽含
    # 「学分」二字但没有「·」，不会误判
    return "·" in line and SUB_CODES.search(line) is not None


def _parse_side(names: str, codes_line: str) -> dict:
    """把一侧的名称行与代码行拆成结构化字段。"""
    name_list = [n.strip() for n in names.split(" + ")]
    code_part, _, credit_part = codes_line.partition("·")
    code_list = [c.strip() for c in code_part.split("/")]
    credits = re.findall(r"[\d.]+", credit_part.split("学分")[0])
    return {
        "names": name_list,
        "codes": code_list,
        "credits": [float(c) for c in credits],
        "raw": codes_line,
    }


def parse_substitute_pdf(pdf_path: Path) -> tuple[list[dict], list[str]]:
    """解析替代关系切片，返回 (CS 相关替代组, 未匹配记录)。

    原表 749 组在 PDF 中以两个视角各出现一次（按替代方 / 被替代方排序），
    切片只保留按替代方排序的视图（原表第 2-62 页）；同 seq 去重。
    """
    reader = PdfReader(str(pdf_path))
    groups: dict[int, dict] = {}
    unmatched: list[str] = []
    for slice_page, page in enumerate(reader.pages, start=1):
        original_page = SUBSTITUTE_PAGES[slice_page - 1]
        lines = [ln.strip() for ln in (page.extract_text() or "").splitlines() if ln.strip()]
        i = 0
        while i < len(lines):
            m = SUB_HEAD.match(lines[i])
            if not m or int(m.group(1)) > 749:
                i += 1
                continue
            seq = int(m.group(1))
            j = i + 1
            left_names = [m.group(3)]
            while j < len(lines) and not _is_codes_line(lines[j]):
                left_names.append(lines[j])
                j += 1
            right_names: list[str] = []
            rec: dict | None = None
            if j < len(lines):
                left_codes = lines[j]
                j += 1
                while j < len(lines) and not _is_codes_line(lines[j]):
                    right_names.append(lines[j])
                    j += 1
            if j < len(lines) and right_names:
                right_codes = lines[j]
                j += 1
                if j < len(lines) and (
                    lines[j].startswith("↔") or lines[j].startswith("→")
                ):
                    direction = lines[j]
                    j += 1
                    rec = {
                        "seq": seq,
                        "group_id": m.group(2),
                        "substitute": _parse_side(" ".join(left_names), left_codes),
                        "original": _parse_side(" ".join(right_names), right_codes),
                        "direction": "可相互替代" if direction.startswith("↔") else "单向（左→右）",
                        "is_course_group": "课程组" in direction,
                        "slice_page": slice_page,
                        "original_page": original_page,
                    }
            if rec is None:
                unmatched.append(f"切片p{slice_page}: {lines[i][:60]}")
                i += 1
                continue
            i = j
            codes_all = rec["substitute"]["codes"] + rec["original"]["codes"]
            if any(CS_CODE.match(c) for c in codes_all):
                groups.setdefault(seq, rec)
    return list(groups.values()), unmatched


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

    print("== 替代关系切片 ==")
    pdf = REAL_DIR / "替代关系-计算机切片.pdf"
    subs, unmatched = parse_substitute_pdf(pdf)
    total_unmatched += len(unmatched)
    two_way = sum(1 for g in subs if g["direction"] == "可相互替代")
    grouped = sum(1 for g in subs if g["is_course_group"])
    print(
        f"替代关系-计算机切片.pdf: CS 相关替代组 {len(subs)} 组 "
        f"(可相互替代 {two_way} / 单向 {len(subs) - two_way}, 课程组 {grouped} 组), "
        f"疑似漏匹配 {len(unmatched)} 条"
    )
    for u in unmatched:
        print(f"    待核对 {u}")
    payload = meta("real/替代关系-计算机切片.pdf", "课程替代关系解析与方向/课程组建模场景")
    payload["_meta"]["note"] += " original_page 为原表页码；方向恒为替代方 → 被替代方。"
    payload["substitutions"] = subs
    dump_yaml(payload, EXPECTED_DIR / "替代关系-计算机切片.yaml")

    print(f"\n合计疑似漏匹配 {total_unmatched} 行（需人工核对）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
