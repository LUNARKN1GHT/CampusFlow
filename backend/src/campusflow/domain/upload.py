"""上传校验领域规则（D003）。

校验允许格式、真实类型（魔数）、大小；扩展名与文件头必须一致，
伪造扩展名（如把文本改名为 .pdf）被拒绝。
"""

from dataclasses import dataclass

# 允许的上传类型：扩展名 → (MIME 类型, 魔数前缀)
_UPLOAD_RULES = {
    "pdf": ("application/pdf", b"%PDF"),
    "png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    "jpg": ("image/jpeg", b"\xff\xd8\xff"),
    "jpeg": ("image/jpeg", b"\xff\xd8\xff"),
}


@dataclass(frozen=True)
class UploadValidation:
    """校验通过的结果：净化后的扩展名与真实类型。"""

    extension: str
    mime_type: str


def validate_upload(
    filename: str, content: bytes, max_bytes: int
) -> tuple[UploadValidation | None, str | None]:
    """校验上传文件。通过返回 (结果, None)，失败返回 (None, 中文错误信息)。"""
    if not content:
        return None, "文件内容为空"
    if len(content) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        return None, f"文件超过大小上限（{limit_mb} MB）"

    if "." not in filename:
        return None, "文件名缺少扩展名"
    extension = filename.rsplit(".", 1)[-1].strip().lower()
    if extension not in _UPLOAD_RULES:
        allowed = "、".join(sorted(_UPLOAD_RULES))
        return None, f"不支持的文件类型 .{extension}（允许：{allowed}）"

    mime_type, magic = _UPLOAD_RULES[extension]
    if not content.startswith(magic):
        return None, f"文件内容与扩展名 .{extension} 不符，请确认文件未损坏"
    return UploadValidation(extension=extension, mime_type=mime_type), None
