"""原文件存储领域规则（D002）。

用户提供的文件名永远不得参与存储键的生成；磁盘路径只由服务端生成的
随机标识与扩展名白名单决定。
"""

# 允许保存的扩展名白名单（不含点号，小写）
ALLOWED_EXTENSIONS = frozenset({"pdf", "png", "jpg", "jpeg", "txt", "docx", "html"})


def sanitize_extension(raw: str) -> str:
    """把用户输入（文件名或扩展名）净化为白名单内的扩展名。

    输入可以是 "作业.PDF"、"pdf" 或 ".pdf"；不在白名单内抛出 ValueError。
    """
    candidate = raw.rsplit(".", 1)[-1] if "." in raw else raw
    candidate = candidate.strip().lower()
    if candidate not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型：{raw}")
    return candidate
