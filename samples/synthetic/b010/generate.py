"""Generate the synthetic text PDF used by the B010 acceptance sample."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "output" / "pdf" / "b010-text-and-cross-page-table.pdf"
PAGE_WIDTH, PAGE_HEIGHT = A4
FONT = "STSong-Light"


def footer(canvas: Canvas, page_number: int) -> None:
    canvas.setFont(FONT, 9)
    canvas.setFillColor(colors.HexColor("#586174"))
    canvas.drawCentredString(
        PAGE_WIDTH / 2, 24, f"CampusFlow 合成验收样本 | 第 {page_number} 页 / 共 3 页"
    )


def draw_notice(canvas: Canvas) -> None:
    canvas.setTitle("CampusFlow B010 synthetic course notice")
    canvas.setAuthor("CampusFlow project - synthetic test data")
    canvas.setFont(FONT, 18)
    canvas.setFillColor(colors.HexColor("#172033"))
    canvas.drawString(56, PAGE_HEIGHT - 70, "数据结构课程通知（合成样本）")

    canvas.setFont(FONT, 10)
    canvas.setFillColor(colors.HexColor("#586174"))
    canvas.drawString(56, PAGE_HEIGHT - 94, "发布方：数据结构课程组（合成）")
    canvas.drawString(
        56,
        PAGE_HEIGHT - 110,
        "发布时间：2026-09-28 09:00 | 适用范围：2026 秋季学期 CS2026-A 班",
    )

    paragraphs = [
        ("作业三", "请完成教材第 4 章第 1、3、6 题，并提交一份 PDF 解答。"),
        ("截止时间", "2026 年 10 月 8 日 18:00（北京时间）。"),
        ("提交方式", "进入课程平台“作业三”入口提交；邮件和群聊文件不计为提交。"),
        ("适用对象", "仅适用于 CS2026-A 班；同名课程的 CS2026-B 班以该班通知为准。"),
        ("补充说明", "附表跨第 2 至第 3 页，表头在续页重复；第 13 行从第 3 页开始。"),
    ]
    y = PAGE_HEIGHT - 154
    for heading, content in paragraphs:
        canvas.setFont(FONT, 12)
        canvas.setFillColor(colors.HexColor("#172033"))
        canvas.drawString(56, y, heading)
        canvas.setFont(FONT, 11)
        canvas.setFillColor(colors.HexColor("#30384a"))
        canvas.drawString(136, y, content)
        y -= 44

    canvas.setStrokeColor(colors.HexColor("#b7c0d2"))
    canvas.roundRect(52, y - 24, PAGE_WIDTH - 104, 58, 6, stroke=1, fill=0)
    canvas.setFont(FONT, 10)
    canvas.drawString(
        64,
        y + 10,
        "定位提示：本页为可复制文本 PDF，不是扫描图片。人工答案应引用具体页码与字段。",
    )
    canvas.drawString(
        64, y - 8, "本文件完全由项目构造，不含真实学生、教师、班级或学校信息。"
    )
    footer(canvas, 1)
    canvas.showPage()


def schedule_rows() -> list[list[str]]:
    rows = []
    for number in range(1, 25):
        week = 2 + number
        item = f"练习 {number:02d}"
        deadline = f"{date(2026, 10, 1) + timedelta(days=number):%Y-%m-%d} 18:00"
        if number == 4:
            item = "实验一报告"
            deadline = "2026-10-08 18:00"
        if number == 19:
            item = "课程项目提案"
            deadline = "2026-11-20 20:00"
        rows.append([str(number), str(week), "数据结构", item, deadline, "CS2026-A"])
    return rows


def draw_table_page(
    canvas: Canvas, page_number: int, rows: list[list[str]], part: str
) -> None:
    canvas.setFont(FONT, 15)
    canvas.setFillColor(colors.HexColor("#172033"))
    canvas.drawString(42, PAGE_HEIGHT - 58, f"课程事项表（{part}）")
    canvas.setFont(FONT, 9)
    canvas.setFillColor(colors.HexColor("#586174"))
    canvas.drawString(
        42, PAGE_HEIGHT - 78, "适用范围：2026 秋季学期 | CS2026-A 班 | 时间均为北京时间"
    )

    data = [["行号", "周次", "课程", "事项", "截止时间", "适用教学班"], *rows]
    table = Table(
        data, colWidths=[38, 38, 78, 112, 118, 82], rowHeights=34, repeatRows=1
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe8f8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#172033")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa6ba")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (1, -1), "CENTER"),
                ("ALIGN", (4, 1), (4, -1), "CENTER"),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f6f8fc")],
                ),
            ]
        )
    )
    table.wrapOn(canvas, PAGE_WIDTH, PAGE_HEIGHT)
    table.drawOn(canvas, 42, PAGE_HEIGHT - 80 - 13 * 34)
    footer(canvas, page_number)
    canvas.showPage()


def generate() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    canvas = Canvas(str(OUTPUT), pagesize=A4, invariant=1)
    draw_notice(canvas)
    rows = schedule_rows()
    draw_table_page(canvas, 2, rows[:12], "第 1 部分，共 2 部分")
    draw_table_page(canvas, 3, rows[12:], "续表，第 2 部分，共 2 部分")
    canvas.save()
    print(OUTPUT)


if __name__ == "__main__":
    generate()
