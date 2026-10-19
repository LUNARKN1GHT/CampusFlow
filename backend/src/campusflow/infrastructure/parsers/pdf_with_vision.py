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
from campusflow.domain.jobs import is_retryable
from campusflow.infrastructure.llm.errors import VisionRecognitionFailure
from campusflow.infrastructure.parsers.pdf_render import render_pdf_page

# 与 PdfDocumentParser 的失败原因保持子串匹配；空白页不会进入视觉回退。
SCAN_PAGE_REASON = "扫描页"


class PdfWithVisionFallbackParser:
    """组合解析器：文本层优先，只对扫描页渲染后调用视觉识别，单页失败不影响其它页。"""

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
        """先完整解析文本层，再逐页补做视觉识别；视觉结果绑定到实际页码。"""

        text_outcome = self._text_parser.parse(content, mime_type)

        fragments: list[ParsedFragment] = []
        failures: list[ParseFailure] = []
        seq = 0

        for fragment in text_outcome.fragments:
            # 重新连续编号，避免文本页与后续回退片段的 seq 冲突。
            fragments.append(
                ParsedFragment(
                    seq=seq,
                    locator=fragment.locator,
                    text=fragment.text,
                    confidence=fragment.confidence,
                    table_group=fragment.table_group,
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
            except (VisionRecognitionFailure, ValueError) as exc:
                # 渲染越界等 ValueError 也只算该页失败；retryable 只由领域错误类型决定。
                failures.append(
                    ParseFailure(
                        locator=ParserLocator(kind="page", page=page),
                        reason=f"扫描页识别失败：{exc}",
                        retryable=is_retryable(exc),
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
                            bbox=item.locator.bbox,  # bbox 保持相对整页的 0-1 坐标
                        ),
                        text=item.text,
                        confidence=item.confidence,
                    )
                )
                seq += 1
            for vision_failure in vision_outcome.failures:
                # 模型自述不可读的区域只记录为失败，不据此生成任何确定的片段。
                failures.append(
                    ParseFailure(
                        locator=ParserLocator(kind="page", page=page),
                        reason=f"扫描页部分区域不可读：{vision_failure.reason}",
                        retryable=vision_failure.retryable,
                    )
                )

        return ParseOutcome(fragments=fragments, failures=failures)
