"""Generate the synthetic table screenshot used by the D013 evaluation sample.

需要 Pillow：cd backend && uv run --with pillow ../samples/synthetic/d013/generate.py
Windows 环境使用微软雅黑字体。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUTPUT = Path(__file__).resolve().parent
FONT_PATH = Path("C:/Windows/Fonts/msyh.ttc")


def font(size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size=size, index=index)


def main() -> None:
    image = Image.new("RGB", (1200, 800), "#f6f3ea")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (56, 48, 1144, 752), radius=12, fill="#fffefa", outline="#c8c2b4", width=3
    )
    draw.text((100, 96), "数据结构作业安排（合成）", font=font(42), fill="#20242c")
    draw.text((100, 156), "2026 秋季 | DS2026-A | 第 1 页 / 共 1 页", font=font(22), fill="#5d6572")

    # 表格：3 行 × 4 列
    rows = [
        ("作业", "内容", "截止时间", "提交方式"),
        ("作业一", "链表与栈练习", "2026 年 10 月 20 日 18:00", "课程平台"),
        ("作业二", "二叉树遍历实验", "2026 年 10 月 27 日 18:00", "课程平台"),
    ]
    left, top, width, row_h = 100, 230, 1000, 96
    col_w = [220, 340, 320, 120]
    for r, row in enumerate(rows):
        y = top + r * row_h
        draw.line((left, y, left + width, y), fill="#8b93a1", width=2)
        x = left
        for c, value in enumerate(row):
            draw.text((x + 12, y + 26), value, font=font(26), fill="#263044")
            x += col_w[c]
            draw.line((x, y, x, y + row_h), fill="#c8c2b4", width=1)
        draw.line((left, y + row_h, left + width, y + row_h), fill="#8b93a1", width=2)
    draw.line((left, top, left, top + len(rows) * row_h), fill="#8b93a1", width=2)

    draw.text((100, 660), "合成样本：无真实人员或学校信息", font=font(20), fill="#707784")
    image.save(OUTPUT / "table-screenshot.png", optimize=True)
    print("已生成 table-screenshot.png")


if __name__ == "__main__":
    main()
