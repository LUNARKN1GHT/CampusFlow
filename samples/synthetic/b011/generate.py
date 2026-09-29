"""Generate synthetic scan and screenshot edge-case samples for B011."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUTPUT = Path(__file__).resolve().parent
FONT_PATH = Path("/System/Library/Fonts/STHeiti Light.ttc")


def font(size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size=size, index=index)


def paper() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (1200, 1600), "#f6f3ea")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (56, 48, 1144, 1552), radius=12, fill="#fffefa", outline="#c8c2b4", width=3
    )
    return image, draw


def header(draw: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    draw.text((100, 96), title, font=font(44), fill="#20242c")
    draw.text((100, 158), subtitle, font=font(23), fill="#5d6572")
    draw.line((100, 208, 1100, 208), fill="#aab1bd", width=2)


def readable_scan() -> None:
    image, draw = paper()
    header(
        draw, "算法设计课程通知（合成）", "2026 秋季 | ALG2026-A | 第 1 页 / 共 1 页"
    )
    sections = [
        ("事项", "阅读第 5 章并提交动态规划练习。"),
        ("截止", "2026 年 10 月 12 日 21:00（北京时间）"),
        ("提交", "课程平台“动态规划练习”入口"),
        ("备注", "本通知仅适用于 ALG2026-A 教学班。"),
    ]
    y = 286
    for label, value in sections:
        draw.text((112, y), label, font=font(27), fill="#263044")
        draw.text((270, y), value, font=font(27), fill="#263044")
        draw.line((112, y + 54, 1088, y + 54), fill="#d6d2c8", width=2)
        y += 118
    draw.text(
        (112, 1434), "合成样本：无真实人员或学校信息", font=font(21), fill="#707784"
    )
    image.save(OUTPUT / "readable-scan.png", optimize=True)


def blurred_partial_scan() -> None:
    image, draw = paper()
    header(
        draw, "数据库系统实验通知（合成）", "2026 秋季 | DB2026-A | 第 1 页 / 共 2 页"
    )
    draw.text((112, 284), "实验二：事务隔离级别", font=font(31), fill="#263044")
    draw.text(
        (112, 348), "截止：2026 年 10 月 16 日 18:00", font=font(27), fill="#263044"
    )
    draw.text((112, 408), "适用教学班：DB2026-A", font=font(27), fill="#263044")
    draw.line((112, 474, 1088, 474), fill="#d6d2c8", width=2)
    lines = [
        "提交内容：实验报告、SQL 脚本与运行截图。",
        "评分细则：正确性、说明完整性、结果可复现性。",
        "后续说明和补交规则位于本页下半部分。",
        "这一段故意模拟失焦扫描，不能作为可靠事实来源。",
        "未覆盖字段包括补交期限、扣分比例和联系人。",
    ]
    y = 560
    for line in lines:
        draw.text((112, y), line, font=font(27), fill="#303744")
        y += 86

    blurred = image.crop((72, 520, 1128, 1430)).filter(
        ImageFilter.GaussianBlur(radius=10)
    )
    image.paste(blurred, (72, 520))
    overlay = ImageDraw.Draw(image)
    overlay.rectangle((72, 520, 1128, 1430), outline="#bd5b56", width=5)
    overlay.text(
        (88, 1450), "红框区域为故意失焦的未覆盖范围", font=font(21), fill="#9a302d"
    )
    image.save(OUTPUT / "blurred-partial-scan.png", optimize=True)


def missing_pages_scan() -> None:
    image, draw = paper()
    header(
        draw, "课程实验手册（合成节选）", "2026 秋季 | NET2026-A | 第 2 页 / 共 3 页"
    )
    draw.rectangle((100, 260, 1100, 340), fill="#e8edf6")
    draw.text((124, 278), "本页可读内容：实验环境与步骤", font=font(29), fill="#263044")
    steps = [
        "1. 启动本地抓包环境，不使用真实账号。",
        "2. 导入随样本提供的合成数据包。",
        "3. 记录过滤表达式和观察结果。",
        "4. 截图中不得包含设备标识或个人地址。",
    ]
    y = 404
    for step in steps:
        draw.text((124, y), step, font=font(27), fill="#303744")
        y += 90
    draw.rounded_rectangle(
        (102, 1000, 1098, 1300), radius=16, outline="#b24f48", width=5
    )
    draw.text((136, 1044), "缺页提示", font=font(31), fill="#9a302d")
    draw.text(
        (136, 1112),
        "第 1 页（实验目标与提交要求）未提供。",
        font=font(27),
        fill="#303744",
    )
    draw.text(
        (136, 1172),
        "第 3 页（截止时间与评分规则）未提供。",
        font=font(27),
        fill="#303744",
    )
    draw.text(
        (136, 1232),
        "不得据此推断截止时间或完整提交要求。",
        font=font(27),
        fill="#303744",
    )
    image.save(OUTPUT / "missing-pages-scan.png", optimize=True)


def chat_screenshot() -> None:
    image = Image.new("RGB", (900, 1200), "#eef1f5")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 900, 120), fill="#263044")
    draw.text((54, 36), "算法设计课程群（合成截图）", font=font(30), fill="#ffffff")
    draw.text((54, 138), "2026-09-29", font=font(20), fill="#667085")

    bubbles = [
        (54, 205, 730, 370, "课程助教（合成）\n本周练习已发布，适用于 ALG2026-A 班。"),
        (168, 424, 846, 598, "同学 A（合成）\n请问截止时间是周五晚上吗？"),
        (
            54,
            650,
            776,
            884,
            "课程助教（合成）\n请以课程平台为准。截图只显示到“周五”，\n没有年份、具体时刻和提交入口。",
        ),
        (54, 1014, 846, 1280, "课程助教（合成）\n补充要求如下：请先完成……"),
    ]
    for index, (left, top, right, bottom, text) in enumerate(bubbles):
        fill = "#ffffff" if index != 1 else "#dce9ff"
        draw.rounded_rectangle(
            (left, top, right, bottom), radius=24, fill=fill, outline="#cbd3df", width=2
        )
        draw.multiline_text(
            (left + 28, top + 24), text, font=font(25), fill="#263044", spacing=14
        )

    draw.rectangle((0, 1168, 900, 1200), fill="#bd5b56")
    draw.text((292, 1169), "截图在此处截断", font=font(20), fill="#ffffff")
    image.save(OUTPUT / "cropped-chat-screenshot.png", optimize=True)


def generate() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    readable_scan()
    blurred_partial_scan()
    missing_pages_scan()
    chat_screenshot()
    print("generated 4 B011 images")


if __name__ == "__main__":
    generate()
