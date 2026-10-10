"""扫描 PDF 逐页识别回退（D015，D011 协议组合实现）。

先走文本解析；只对"扫描页无文字层"的失败页渲染为图片并调用视觉识别，
识别结果绑定到实际页码。单页识别失败只影响该页，不抹掉其他成功页面。
"""

from __future__ import annotations

from campusflow.application.ports.parsers import (
    DocumentParser,
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.infrastructure.llm.errors import VisionRecognitionError
from campusflow.infrastructure.parsers.pdf_render import render_pdf_page

SCAN_PAGE_REASON = "扫描页"


class PdfWithVisionFallbackParser:
    def __init__(
        self,
        text_parser: DocumentParser,
        vision_parser: DocumentParser,
        *,
        render=render_pdf_page,
    ) -> None:
        self._text_parser = text_parser
        self._vision_parser = vision_parser
        self._render = render

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        text_outcome = self._text_parser.parse(content, mime_type)

        fragments: list[ParsedFragment] = []
        failures: list[ParseFailure] = []
        seq = 0

        text_pages = {f.locator.page for f in text_outcome.fragments}
        for fragment in text_outcome.fragments:
            fragments.append(
                ParsedFragment(
                    seq=seq,
                    locator=fragment.locator,
                    text=fragment.text,
                    confidence=fragment.confidence,
                )
            )
            seq += 1

        for failure in text_outcome.failures:
            page = failure.locator.page
            if page is None or SCAN_PAGE_REASON not in failure.reason:
                # 空白页等非扫描失败原样保留
                failures.append(failure)
                continue
            # 扫描页：渲染为图片并调用视觉识别回退
            try:
                image = self._render(content, page)
                vision_outcome = self._vision_parser.parse(image, "image/png")
            except (VisionRecognitionError, ValueError) as exc:
                failures.append(
                    ParseFailure(
                        locator=ParserLocator(kind="page", page=page),
                        reason=f"扫描页识别失败：{exc}",
                    )
                )
                continue
            for item in vision_outcome.fragments:
                fragments.append(
                    ParsedFragment(
                        seq=seq,
                        locator=ParserLocator(
                            kind=item.locator.kind,
                            page=page,  # 回退结果绑定到实际页码
                            bbox=item.locator.bbox,
                        ),
                        text=item.text,
                        confidence=item.confidence,
                    )
                )
                seq += 1
            for vision_failure in vision_outcome.failures:
                failures.append(
                    ParseFailure(
                        locator=ParserLocator(kind="page", page=page),
                        reason=f"扫描页部分区域不可读：{vision_failure.reason}",
                    )
                )

        return ParseOutcome(fragments=fragments, failures=failures)
