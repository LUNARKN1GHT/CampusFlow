# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pypdf==6.19.0", "PyYAML==6.0.3"]
# ///
"""C001 预期标注的机器辅助核对脚本。

对 samples/course-paths/expected/ 下的每份 YAML：
1. 全量校验：每条记录的字段必须能在其声明页码的同一行原文中找到（防页码错标、
   字段串行、文件张冠李戴）。
2. 分层抽样：按固定随机种子抽取若干条，打印 YAML 记录与原文行对照，供人工目检。

用法：
    uv run scripts/verify_course_path_expectations.py
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = REPO_ROOT / "samples" / "course-paths"
SAMPLE_SIZE = 6  # 每份文件抽样条数
SEED = 183
SUBSTITUTE_PAGES = [
    2,
    3,
    10,
    11,
    12,
    13,
    23,
    24,
    25,
    26,
    28,
    29,
    33,
    34,
    46,
    47,
    48,
    49,
    62,
]


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def number(value: object) -> str:
    return format(float(value), "g")


def page_lines(pdf_path: Path) -> dict[int, list[str]]:
    reader = PdfReader(str(pdf_path))
    return {
        i + 1: [
            ln.strip() for ln in (p.extract_text() or "").splitlines() if ln.strip()
        ]
        for i, p in enumerate(reader.pages)
    }


def verify_plan(yaml_path: Path) -> tuple[list[str], list[tuple[dict, str]]]:
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    pdf = BASE / payload["_meta"]["source"]
    lines = page_lines(pdf)
    errors: list[str] = []
    pairs: list[tuple[dict, str]] = []
    for c in payload.get("courses", []):
        hay = lines.get(c["page"], [])
        expected = compact(
            f"{c['code']} {c['name']} {c['kind']} {c['hours']} "
            f"{number(c['credits'])} {c['unit']} {c['grade']} {c['semester']}"
        )
        hit = next(
            (ln for ln in hay if compact(ln) == expected),
            None,
        )
        if hit is None:
            errors.append(
                f"{yaml_path.name} 课程 {c['code']} 第 {c['page']} 页未找到对应原文行"
            )
        else:
            pairs.append((c, hit))
    for g in payload.get("groups", []):
        hay = lines.get(g["page"], [])
        hit = next(
            (
                ln
                for ln in hay
                if compact(ln)
                == compact(
                    f"{g['group']}要求学分:{g['required_credits']} 要求门数:{g['required_count']}"
                )
            ),
            None,
        )
        if hit is None:
            errors.append(
                f"{yaml_path.name} 分组 {g['group']!r} 第 {g['page']} 页未找到对应原文行"
            )
        else:
            pairs.append((g, hit))
    for raw in payload.get("rows_needing_review", []):
        match = re.fullmatch(r"p(\d+): (.+)", raw)
        if match is None or match[2] not in lines.get(int(match[1]), []):
            errors.append(f"{yaml_path.name} 待核对原文不匹配：{raw}")
    return errors, pairs


def verify_relations(yaml_path: Path) -> tuple[list[str], list[tuple[dict, str]]]:
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    pdf = BASE / payload["_meta"]["source"]
    lines = page_lines(pdf)
    errors: list[str] = []
    pairs: list[tuple[dict, str]] = []
    for r in payload.get("relations", []):
        hay = lines.get(r["slice_page"], [])
        expected = compact(
            " ".join(
                str(r[k])
                for k in (
                    "seq",
                    "course_code",
                    "course_name",
                    "unit",
                    "prereq_name",
                    "prereq_code",
                    "prereq_last_semester",
                    "prereq_status",
                )
                if r[k] is not None
            )
        )
        hit = next(
            (
                ln
                for ln in hay
                if compact(ln) == expected
                and r["original_page"] == r["slice_page"] + 13
            ),
            None,
        )
        if hit is None:
            errors.append(
                f"{yaml_path.name} 关系 {r['seq']} 切片第 {r['slice_page']} 页未找到对应原文行"
            )
        else:
            pairs.append((r, hit))
    return errors, pairs


def verify_substitutions(yaml_path: Path) -> tuple[list[str], list[tuple[dict, str]]]:
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    pdf = BASE / payload["_meta"]["source"]
    lines = page_lines(pdf)
    errors: list[str] = []
    pairs: list[tuple[dict, str]] = []
    for g in payload.get("substitutions", []):
        hay = lines.get(g["slice_page"], [])
        head_hit = next(
            (ln for ln in hay if ln.startswith(f"{g['seq']} #{g['group_id']} ")),
            None,
        )
        # 限定在同一组的连续原文块，不能借用同页其他组的方向或代码。
        block = []
        if head_hit is not None:
            start = hay.index(head_hit)
            end = next(
                (
                    i
                    for i in range(start + 1, len(hay))
                    if re.match(r"^\d+ #\d+ ", hay[i])
                ),
                len(hay),
            )
            block = hay[start:end]
        ok = bool(block) and 1 <= g["slice_page"] <= len(SUBSTITUTE_PAGES)
        if ok:
            ok = g["original_page"] == SUBSTITUTE_PAGES[g["slice_page"] - 1]
        cursor = 0
        for side_name in ("substitute", "original"):
            side = g[side_name]
            try:
                raw_index = block.index(side["raw"], cursor)
                names = block[cursor:raw_index]
                if cursor == 0:
                    names[0] = re.sub(r"^\d+ #\d+\s+", "", names[0])
                codes, credits = side["raw"].split("·", 1)
                credit_values = [
                    float(v)
                    for v in re.findall(r"\d+(?:\.\d+)?", credits.split("学分")[0])
                ]
                ok = ok and compact(" ".join(names)) == compact(
                    " + ".join(side["names"])
                )
                ok = ok and [v.strip() for v in codes.split("/")] == side["codes"]
                ok = ok and credit_values == side["credits"]
                cursor = raw_index + 1
            except (ValueError, IndexError):
                ok = False
        direction = block[cursor] if cursor < len(block) else ""
        expected_direction = (
            "可相互替代" if direction.startswith("↔") else "单向（左→右）"
        )
        ok = (
            ok
            and direction.startswith(("↔", "→"))
            and g["direction"] == expected_direction
            and ("课程组" in direction) == g["is_course_group"]
        )
        if not ok:
            errors.append(
                f"{yaml_path.name} 替代组 {g['seq']} 切片第 {g['slice_page']} 页未找到对应原文"
            )
        else:
            pairs.append((g, head_hit))
    return errors, pairs


def sample_pairs(pairs: list[tuple[dict, str]], key: str) -> None:
    rng = random.Random(SEED)
    picked = rng.sample(pairs, min(SAMPLE_SIZE, len(pairs)))
    for entry, raw in picked:
        label = entry.get("code") or entry.get("seq")
        print(f"  [{key} #{label}]")
        print(f"    YAML: {entry}")
        print(f"    原文: {raw}")


def main() -> int:
    all_errors: list[str] = []
    for yaml_path in sorted((BASE / "expected").glob("*.yaml")):
        print(f"== {yaml_path.name} ==")
        if "预修关系" in yaml_path.name:
            errors, pairs = verify_relations(yaml_path)
        elif "替代关系" in yaml_path.name:
            errors, pairs = verify_substitutions(yaml_path)
        else:
            errors, pairs = verify_plan(yaml_path)
        print(f"  全量校验 {len(pairs)} 条通过, {len(errors)} 条失败")
        all_errors.extend(errors)
        sample_pairs(pairs, yaml_path.stem)
        print()
    if all_errors:
        print("存在校验失败记录：")
        for e in all_errors:
            print(f"  {e}")
        return 1
    print("全量字段校验全部通过；上方抽样供人工目检。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
