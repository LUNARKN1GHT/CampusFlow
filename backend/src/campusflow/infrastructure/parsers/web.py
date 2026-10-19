"""N006：仅解析已保存的快照，不联网、不执行网页脚本。

正文按原始 HTML 元素编号定位（清洗前编号），媒体与嵌入内容只报告未覆盖范围；
登录页、脚本渲染页和编码声明不符的快照明确失败，不猜测正文。
"""

import re
from collections import Counter
from email.message import Message

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.domain.errors import PermanentError

_BLOCKS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "pre",
    "blockquote",
    "td",
    "th",
    "dt",
    "dd",
    "caption",
    "div",
    "section",
    "article",
    "main",
    "body",
    "header",
    "time",
}
# 明确已知的导航/广告/隐藏容器；只删这些区块，不按文字长度猜测正文。
_NOISE = (
    "head, nav, aside, footer, script, style, noscript, form, template, "
    "[hidden], [aria-hidden='true'], [role='navigation'], "
    ".advertisement, .advert, .ads, .ad, .sidebar, .breadcrumb, .breadcrumbs, "
    "#advertisement, #sidebar, #ad, .nav, .navbar, .menu, #nav, #menu"
)


def _decode(content: bytes, mime_type: str) -> tuple[str, str]:
    """按 HTTP 头或 meta 声明严格解码；缺失时按 UTF-8，声明不可用或字节不符即拒绝。"""

    header = Message()
    header["content-type"] = mime_type
    kind = header.get_content_type()
    if kind not in ("text/html", "application/xhtml+xml", "text/plain"):
        raise PermanentError("网页正文类型不支持，请上传文件或粘贴原文")
    encoding = header.get_content_charset()
    if not encoding and kind != "text/plain":
        # 只读取 meta 标签中的声明，不把正文里出现的 charset 当成编码。
        for meta in re.findall(rb"<meta\b[^>]*>", content[:8192], re.I):
            match = re.search(rb"charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)", meta, re.I)
            if match:
                encoding = match[1].decode("ascii")
                break
    try:
        # 严格解码：编码声明与实际字节不符时报错，而不是替换成含义未知的字符。
        return content.decode(encoding or "utf-8-sig", errors="strict"), kind
    except (LookupError, UnicodeError):
        raise PermanentError("网页编码缺失或与内容不符，无法可靠读取；请粘贴原文") from None


class WebDocumentParser:
    """把保存的 HTML/纯文本快照转为带原始位置的片段；登录页与无正文页面明确失败。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        """解析一份已保存快照；HTML 按清洗前的元素序号定位，纯文本按空行分段。"""

        # 快照大小上限与抓取上限一致，避免在解析阶段处理超大文本。
        if len(content) > 2 * 1024 * 1024:
            raise PermanentError("网页快照超过 2 MiB 解析上限")
        text, kind = _decode(content, mime_type)
        if kind == "text/plain":
            # 纯文本按空行分段；空段不产出片段，但段落号仍按切分位置计数。
            fragments = [
                ParsedFragment(i, ParserLocator("paragraph", paragraph=n), part.strip())
                for i, (n, part) in enumerate(
                    item
                    for item in enumerate(re.split(r"\r?\n\s*\r?\n", text), 1)
                    if item[1].strip()
                )
            ]
            return self._outcome(fragments, [])

        soup = BeautifulSoup(text, "html.parser")
        counts: Counter[str] = Counter()
        anchors: dict[int, str] = {}
        table_numbers: dict[int, int] = {}
        # 在删除菜单等内容之前编号。引用始终指向原始快照，而非清洗后的序号。
        for tag in soup.find_all(True):
            counts[tag.name] += 1
            anchors[id(tag)] = f"{tag.name}:{counts[tag.name]}"
            if tag.name == "table":
                table_numbers[id(tag)] = counts["table"]

        # HTTP 200 也可能是登录页：识别密码框或登录表单后拒绝导入，不当作通知正文。
        if soup.select_one("input[type='password' i]") or any(
            re.search(r"登录|登入|sign\s*in|log\s*in", form.get_text(" ", strip=True), re.I)
            for form in soup.find_all("form")
        ):
            return ParseOutcome(
                [],
                [
                    ParseFailure(
                        ParserLocator("document"),
                        "网页包含登录表单，未作为通知正文导入；请上传或粘贴原文",
                    )
                ],
            )

        # 编号已经记录，删除导航/广告不会改变引用指向的原始元素位置。
        for tag in list(soup.select(_NOISE)):
            if tag.parent is not None:
                tag.decompose()
        roots = soup.select("article, main, [role='main'], .article-content, #article-content")
        # 多个正文容器候选取文字最多者，避免把侧栏或推荐区当作正文。
        root = (
            max(roots, key=lambda tag: len(tag.get_text(strip=True)))
            if roots
            else soup.body or soup
        )
        failures = []
        media = ["img", "iframe", "object", "embed", "canvas", "video", "audio", "svg"]
        # 媒体与嵌入内容不加载、不识别，只逐项报告未覆盖范围（定位到原始元素）。
        for tag in root.find_all(media):
            failures.append(
                ParseFailure(
                    ParserLocator("web", anchor=anchors[id(tag)]),
                    "正文含图片、音视频或嵌入内容，未识别其中信息，也未加载外部资源",
                )
            )
        for tag in list(root.find_all(media)):
            if tag.parent is not None:
                tag.decompose()
        if not root.get_text(strip=True) and not failures:
            failures.append(
                ParseFailure(
                    ParserLocator("document"),
                    "网页没有可读取正文，可能需要运行脚本；请上传或粘贴原文",
                )
            )

        # 部分通知把发布单位/日期放在 main 外的页眉；保留原文，不自动推断日期字段。
        selected_roots = {id(root)}
        selected_roots.update(
            id(tag) for tag in soup.select("header h1, header .meta, header time")
        )
        owners: dict[int, tuple[Tag, list[str]]] = {}
        for node in soup.descendants:
            if (
                not isinstance(node, NavigableString)
                or isinstance(node, Comment)
                or not node.strip()
            ):
                continue
            # 单元格中的段落合为一个可定位片段，嵌套表格使用最近的单元格。
            parents = list(node.parents)
            if not any(id(parent) in selected_roots for parent in parents):
                continue
            owner = next((p for p in parents if p.name in ("td", "th")), None)
            if owner is None:
                # 非表格文本归到最近的块级元素；没有合适容器时归到正文根。
                owner = next((p for p in parents if p.name in _BLOCKS), root)
            owners.setdefault(id(owner), (owner, []))[1].append(str(node).strip())

        fragments = []
        for owner, parts in owners.values():
            anchor = anchors.get(id(owner))
            # 正文根与块元素通常有清洗前编号；取不到时退回文档级定位，不编造序号。
            locator = ParserLocator("web", anchor=anchor) if anchor else ParserLocator("document")
            table = owner.find_parent("table")
            # 表格编号同样来自清洗前统计，跨片段仍指向同一张原始表格。
            group = f"web-table-{table_numbers[id(table)]}" if table is not None else None
            fragments.append(
                ParsedFragment(len(fragments), locator, " ".join(parts), table_group=group)
            )
        return self._outcome(fragments, failures)

    @staticmethod
    def _outcome(fragments: list[ParsedFragment], failures: list[ParseFailure]) -> ParseOutcome:
        """兜底：既无片段也无失败说明时补一条“没有可读取正文”，避免静默成功。"""

        if not fragments and not failures:
            failures.append(
                ParseFailure(
                    ParserLocator("document"),
                    "网页没有可读取正文，可能需要运行脚本；请上传或粘贴原文",
                )
            )
        return ParseOutcome(fragments, failures)
