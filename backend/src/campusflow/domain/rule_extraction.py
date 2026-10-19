"""规则原文完整性检查：不解释或改写规定，不丢弃句内否定、条件和例外。

只做字面检查：确认正文与限定语句能在原文中完整找到，不解析生效日期。
"""

import re

from campusflow.domain.errors import DomainError


def require_effective_basis(text: str) -> None:
    """资料时间戳不能自行成为规则生效依据；明确声明按发布日起生效则保留原文。"""
    if re.search(r"发布时间|发布日期|发布于|上传时间|导入时间", text) and not re.search(
        r"生效|失效|废止|施行|实施|执行|试行|有效期|(?:自|从).*(?:起|开始)", text
    ):
        raise DomainError("不能把资料发布、上传或导入时间当成规则生效依据")


def require_full_rule(text: str, sources: list[str]) -> None:
    """规则正文必须保留一个被引用片段的全文，不能只摘取结论子句。"""
    if not text.strip() or not any(text.strip() == source.strip() for source in sources):
        raise DomainError("规则正文必须保留对应原文片段全文，不能删去条件、否定或例外")


def require_complete_sentence(text: str, sources: list[str]) -> None:
    """条件/范围/生效时间保留完整语句；不把逗号、分号当作可截断条件的边界。"""
    excerpt = text.strip()
    if not excerpt:
        raise DomainError("规则限定语句不能为空")
    boundaries = "。！？!?\n"
    for source in sources:
        for match in re.finditer(re.escape(excerpt), source):
            before = source[: match.start()].rstrip(" \t\r")
            after = source[match.end() :].lstrip(" \t\r")
            starts_sentence = not before or before[-1] in boundaries
            ends_sentence = not after or excerpt[-1] in boundaries or after[0] in boundaries
            if starts_sentence and ends_sentence:
                return
    raise DomainError("条件、范围与生效信息必须保留完整语句，不得截掉否定或限定前后文")
