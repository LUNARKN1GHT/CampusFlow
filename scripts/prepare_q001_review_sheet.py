"""Q001：把待人工复核的三类表格整理成便于阅读和填写的对照表。

背景（见 docs/evaluation/q001-live-*/）：
原始复核表为了保护证据、也为了不被表格软件误当作公式执行，把检索到的原文、
模型回答和人工预标注都塞进 JSON 文本；它们本身就是证据文件，不能改写。
结果是一行里密密麻麻全是转义字符，非作者复核时很难逐条比对。

Q001 待复核的其实有两件事，本脚本各出一份读得懂的对照表：

1. 逐条回答复核（161 条）：模型答得对不对、有没有踩红线；
2. 人工标注复核（9 条）：作者写的「标准答案」本身对不对。

两份对照表都只读原始文件另写新文件：
- 把 JSON 展开成中文可读的多行文本；
- 保留来源文件与行号，便于回填；
- 把脚本已经自动查出的问题（坏引用编号、检索命中情况）摆在表上，
  复核人不必自己重查一遍；语义判断仍留给人，机器不替人签字；
- 新增与原始表同名同含义的待填列，复核人直接在这份表里填写。

原始表本脚本绝不改写。用法（仓库根目录执行）：

    uv run --locked --project backend python scripts/prepare_q001_review_sheet.py

产出：
    docs/evaluation/q001-review-worksheet.csv             逐条回答对照表（161 行）
    docs/evaluation/q001-annotation-review-worksheet.csv  人工标注对照表（9 行）

第二批（`cases-v2.json`，同课程干扰资料与未见问题）另出一套表，用同一脚本指定来源：

    uv run --locked --project backend python scripts/prepare_q001_review_sheet.py \
        --experiment "v4-第二批=docs/evaluation/q001-live-batch2/answer-review.csv=四组各答 15 题" \
        --annotation-source docs/evaluation/q001-live-batch2/annotation-review.csv \
        --output docs/evaluation/q001-batch2-review-worksheet.csv \
        --annotation-output docs/evaluation/q001-batch2-annotation-review-worksheet.csv

不给 `--experiment` 时仍用内置的三轮历史实验，产出与原来逐字节相同。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 三个实验，顺序即复核顺序：先看正常回答，再看格式不合格，最后看针对性复测。
# q001-live-001 是首轮中断快照，其 20 条已全部包含在 live-comparison 里，不重复评。
EXPERIMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "v1-首轮",
        "docs/evaluation/q001-live-comparison/answer-review.csv",
        "四组配置各回答 9 题，含 13 条格式不合格与 1 条网络失败",
    ),
    (
        "v2-严格编号",
        "docs/evaluation/q001-live-strict-comparison/answer-review.csv",
        "格式与编号要求收紧后重跑，72 条格式全部通过",
    ),
    (
        "v3-状态定向",
        "docs/evaluation/q001-live-status-comparison/answer-review.csv",
        "只针对状态定义有出入的 16 条定向复测",
    ),
)

WORKSHEET_PATH = REPO_ROOT / "docs" / "evaluation" / "q001-review-worksheet.csv"

# 人工标注复核：同一套 9 个样本在四个实验目录里都有导出，内容一致；
# q001-live-status-comparison 只含定向复测的 2 个样本，是这份的子集，不单独出表。
ANNOTATION_SOURCE = "docs/evaluation/q001-live-comparison/annotation-review.csv"
ANNOTATION_PATH = REPO_ROOT / "docs" / "evaluation" / "q001-annotation-review-worksheet.csv"

# 与原始复核表同名同含义的待填列，复核人在这份表里填写后由维护者写回原始表。
FILL_COLUMNS = (
    "supported_claims",
    "total_claims",
    "correct_uncertainties",
    "output_uncertainties",
    "hard_failures",
    "reviewer",
    "reviewed_at",
    "notes",
)

HEADER = (
    "序号",
    "实验",
    "来源文件",
    "原表行号",
    "场景",
    "轮次",
    "回答状态",
    "问题",
    "系统检出-坏引用编号",
    "系统检出-检索命中/应有",
    "原资料（编号｜原文）",
    "模型结论",
    "模型主张（含引用编号）",
    "模型不确定提示",
    "人工预标注要点",
    "人工预标注要点依据原文",
    "人工预标注不确定项",
    *FILL_COLUMNS,
)

ANNOTATION_HEADER = (
    "序号",
    "样本版本",
    "场景",
    "问题",
    "适用范围",
    "本次可用资料（编号｜原文）",
    "应当检索到的资料编号",
    "预期结论",
    "人工预标注要点",
    "人工预标注要点依据原文",
    "人工预标注不确定项",
    "annotation_review",
    "reviewer",
    "reviewed_at",
    "notes",
)

# 各状态的含义写进复核说明书（docs/evaluation/q001-review-guide.md），不在代码里留死常量。


def _decode(cell: str) -> object | None:
    """原表把 JSON 又套了一层字符串，逐层解开；解不开就返回 None。"""
    if not cell:
        return None
    try:
        value = json.loads(cell)
    except (TypeError, ValueError):
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _documents(cell: str) -> str:
    docs = _decode(cell)
    if not isinstance(docs, list):
        return ""
    lines = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        locator = doc.get("locator") or ""
        scope = doc.get("scope") or []
        place = f"{locator}" + (f"（{'.'.join(map(str, scope))}）" if scope else "")
        lines.append(f"[{doc.get('id', '?')}] {doc.get('text', '')}")
        if place:
            lines.append(f"      定位：{place}")
    return "\n".join(lines)


def _claims(cell: str) -> str:
    """模型回答里的主张：逐条编号，带上它引用的资料编号。"""
    answer = _decode(cell)
    if not isinstance(answer, dict):
        return ""
    claims = answer.get("claims") or []
    lines = []
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            continue
        citations = "、".join(claim.get("citations") or []) or "无引用"
        lines.append(f"{index}. {claim.get('text', '')}")
        lines.append(f"   引用编号：{citations}")
    return "\n".join(lines)


def _uncertainties(cell: str) -> str:
    answer = _decode(cell)
    if not isinstance(answer, dict):
        return ""
    items = answer.get("uncertainties") or []
    lines = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            reason = item.get("reason", "")
            explanation = item.get("explanation", "")
            lines.append(f"{index}. {reason}")
            if explanation:
                lines.append(f"   说明：{explanation}")
        else:
            lines.append(f"{index}. {item}")
    return "\n".join(lines)


def _disposition(cell: str) -> str:
    answer = _decode(cell)
    if not isinstance(answer, dict):
        return ""
    return str(answer.get("disposition", ""))


def _expected_claims(cell: str) -> str:
    items = _decode(cell)
    if not isinstance(items, list):
        return ""
    return "\n".join(
        f"{index}. {item.get('text', '')}"
        for index, item in enumerate(items, start=1)
        if isinstance(item, dict)
    )


def _expected_quotes(cell: str) -> str:
    items = _decode(cell)
    if not isinstance(items, list):
        return ""
    lines = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        quote = item.get("support_quote", "")
        evidence = "、".join(item.get("evidence") or [])
        lines.append(f"{index}. 「{quote}」 来源：{evidence or '未标注'}")
    return "\n".join(lines)


def _expected_uncertainties(cell: str) -> str:
    items = _decode(cell)
    if not isinstance(items, list):
        return ""
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def _runs(source: str) -> list[dict]:
    """读同目录的结果文件，取出脚本已经自动查出的问题，供复核人参考。

    answer-review.csv 不保存这些字段；离线脚本只会做字面比对，语义判断留给人，
    所以把机器能确定的部分（坏引用编号、检索有没有捞到资料）直接摆到表上。

    两份文件逐行同序且行数相同，但 (config_id, case_id, repeat) 不唯一——
    首轮有一条超时后重试，同一个键出现两次，所以只能按位置对齐，不能按键配对。
    """
    path = (REPO_ROOT / source).parent / "results.json"
    return json.loads(path.read_text(encoding="utf-8"))["runs"]


def _row(experiment: str, source: str, line_no: int, raw: dict, ordinal: int, found: dict) -> dict:
    bad_ids = "、".join(found.get("invalid_citation_ids") or [])
    hits = found.get("retrieval_hits")
    expected = found.get("retrieval_expected")
    return {
        "序号": ordinal,
        "实验": experiment,
        "来源文件": source,
        "原表行号": line_no,
        "场景": raw.get("case_id", ""),
        "轮次": raw.get("repeat", ""),
        "回答状态": raw.get("status", ""),
        "问题": raw.get("question", ""),
        "系统检出-坏引用编号": bad_ids,
        "系统检出-检索命中/应有": "" if hits is None else f"{hits}/{expected}",
        "原资料（编号｜原文）": _documents(raw.get("sources_json", "")),
        "模型结论": _disposition(raw.get("answer_json", "")),
        "模型主张（含引用编号）": _claims(raw.get("answer_json", "")),
        "模型不确定提示": _uncertainties(raw.get("answer_json", "")),
        "人工预标注要点": _expected_claims(raw.get("expected_claims_json", "")),
        "人工预标注要点依据原文": _expected_quotes(raw.get("expected_claims_json", "")),
        "人工预标注不确定项": _expected_uncertainties(raw.get("expected_uncertainties_json", "")),
        **{name: "" for name in FILL_COLUMNS},
    }


def _alignment_key(record: dict) -> tuple[str, str, str, str]:
    return (
        str(record.get("config_id", "")),
        str(record.get("case_id", "")),
        str(record.get("repeat", "")),
        str(record.get("status", "")),
    )


def build(experiments=EXPERIMENTS) -> list[dict]:
    rows: list[dict] = []
    for experiment, source, _ in experiments:
        path = REPO_ROOT / source
        runs = _runs(source)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            records = list(csv.DictReader(stream))
        # 对不齐就直接报错，绝不按错位的行填写复核表。
        if len(records) != len(runs):
            raise ValueError(f"{source}: {len(records)} rows vs {len(runs)} runs")
        for line_no, (raw, run) in enumerate(zip(records, runs, strict=True), start=1):
            if _alignment_key(raw) != _alignment_key(run):
                raise ValueError(f"{source}: row {line_no} does not match its run")
            rows.append(_row(experiment, source, line_no, raw, len(rows) + 1, run))
    return rows


def build_annotation(source: str = ANNOTATION_SOURCE) -> list[dict]:
    """人工标注复核：复核作者写给系统比对的「标准答案」本身对不对。"""
    rows: list[dict] = []
    path = REPO_ROOT / source
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for raw in csv.DictReader(stream):
            scope = _decode(raw.get("scope_json", ""))
            place = " / ".join(map(str, scope)) if isinstance(scope, list) else ""
            rows.append(
                {
                    "序号": len(rows) + 1,
                    "样本版本": raw.get("sample_set_version", ""),
                    "场景": raw.get("case_id", ""),
                    "问题": raw.get("question", ""),
                    "适用范围": place,
                    "本次可用资料（编号｜原文）": _documents(raw.get("eligible_sources_json", "")),
                    "应当检索到的资料编号": "、".join(
                        _decode(raw.get("relevant_ids_json", "")) or []
                    ),
                    "预期结论": raw.get("expected_disposition", ""),
                    "人工预标注要点": _expected_claims(raw.get("expected_claims_json", "")),
                    "人工预标注要点依据原文": _expected_quotes(raw.get("expected_claims_json", "")),
                    "人工预标注不确定项": _expected_uncertainties(
                        raw.get("expected_uncertainties_json", "")
                    ),
                    "annotation_review": "",
                    "reviewer": "",
                    "reviewed_at": "",
                    "notes": "",
                }
            )
    return rows


def _write(path: Path, header: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 中文表头需要 BOM，否则中文版 Excel 打开会乱码；原表同样使用 utf-8-sig。
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(header))
        writer.writeheader()
        writer.writerows(rows)


def _parse_experiment(value: str) -> tuple[str, str, str]:
    """接收「实验名=证据文件=说明」，三段都必填，避免靠猜。"""
    parts = value.split("=")
    if len(parts) != 3 or not all(part.strip() for part in parts):
        raise argparse.ArgumentTypeError("格式应为「实验名=证据文件=说明」")
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 Q001 可读复核对照表")
    parser.add_argument("--output", type=Path, default=WORKSHEET_PATH)
    parser.add_argument("--annotation-output", type=Path, default=ANNOTATION_PATH)
    parser.add_argument(
        "--experiment",
        type=_parse_experiment,
        action="append",
        help="要收进对照表的实验，可重复；不给则用内置的三轮历史实验",
    )
    parser.add_argument(
        "--annotation-source",
        default=ANNOTATION_SOURCE,
        help="人工标注复核的证据文件；不给则用内置的首批标注表",
    )
    args = parser.parse_args(argv)
    experiments = tuple(args.experiment) if args.experiment else EXPERIMENTS

    rows = build(experiments)
    _write(args.output, HEADER, rows)
    for experiment, source, note in experiments:
        count = sum(1 for row in rows if row["来源文件"] == source)
        print(f"{experiment:<12} {count:>3} rows   {source}")
        print(f"{'':<12}           {note}")
    print(f"wrote {_relative(args.output)} ({len(rows)} rows, {len(HEADER)} cols)")

    annotations = build_annotation(args.annotation_source)
    _write(args.annotation_output, ANNOTATION_HEADER, annotations)
    print(
        f"wrote {_relative(args.annotation_output)} "
        f"({len(annotations)} rows, {len(ANNOTATION_HEADER)} cols)  {args.annotation_source}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
