#!/usr/bin/env python3
"""C001 预期标注的机器辅助核对脚本。

对 samples/course-paths/expected/ 下的每份 YAML：
1. 全量校验：每条记录的字段必须能在其声明页码的同一行原文中找到（防页码错标、
   字段串行、文件张冠李戴）。
2. 分层抽样：按固定随机种子抽取若干条，打印 YAML 记录与原文行对照，供人工目检。

用法：
    python3 scripts/verify_course_path_expectations.py
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


def page_lines(pdf_path: Path) -> dict[int, list[str]]:
    reader = PdfReader(str(pdf_path))
    return {
        i + 1: [ln.strip() for ln in (p.extract_text() or "").splitlines() if ln.strip()]
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
        credits = c["credits"]
        credit_forms = {str(credits)}
        if float(credits).is_integer():
            credit_forms.add(str(int(credits)))
        hit = next(
            (
                ln
                for ln in hay
                if ln.startswith(c["code"] + " ")
                and c["name"] in ln
                and c["kind"] in ln
                and any(f" {form} " in f" {ln} " for form in credit_forms)
                and c["unit"] in ln
            ),
            None,
        )
        if hit is None:
            errors.append(f"{yaml_path.name} 课程 {c['code']} 第 {c['page']} 页未找到对应原文行")
        else:
            pairs.append((c, hit))
    for g in payload.get("groups", []):
        hay = lines.get(g["page"], [])
        hit = next(
            (ln for ln in hay if g["group"] in ln and "要求学分" in ln),
            None,
        )
        if hit is None:
            errors.append(f"{yaml_path.name} 分组 {g['group']!r} 第 {g['page']} 页未找到对应原文行")
    return errors, pairs


def verify_relations(yaml_path: Path) -> tuple[list[str], list[tuple[dict, str]]]:
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    pdf = BASE / payload["_meta"]["source"]
    lines = page_lines(pdf)
    errors: list[str] = []
    pairs: list[tuple[dict, str]] = []
    for r in payload.get("relations", []):
        hay = lines.get(r["slice_page"], [])
        hit = next(
            (
                ln
                for ln in hay
                if ln.startswith(str(r["seq"]) + " ")
                and r["course_code"] in ln
                and r["prereq_name"] in ln
                and r["prereq_code"] in ln
                and r["prereq_status"] in ln
            ),
            None,
        )
        if hit is None:
            errors.append(f"{yaml_path.name} 关系 {r['seq']} 切片第 {r['slice_page']} 页未找到对应原文行")
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
        ok = (
            head_hit is not None
            and g["substitute"]["raw"] in hay
            and g["original"]["raw"] in hay
            and any(
                g["direction"].split("（")[0].split(" ")[0][:1] in ln
                and ("可相互替代" in ln or "单向" in ln)
                and ("课程组" in ln) == g["is_course_group"]
                for ln in hay
                if ln.startswith("↔") or ln.startswith("→")
            )
        )
        if not ok:
            errors.append(f"{yaml_path.name} 替代组 {g['seq']} 切片第 {g['slice_page']} 页未找到对应原文")
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
