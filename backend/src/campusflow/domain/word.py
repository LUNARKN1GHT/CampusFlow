"""N002：在保存前检查 DOCX 包结构，所有读取都有大小上限，不解压到磁盘。"""

import io
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 2000
_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def validate_docx(content: bytes) -> str | None:
    """拒绝损坏、宏、加密和异常压缩包；返回可展示的原因。"""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_PACKAGE_ENTRIES:
                return "Word 文件内部项目过多，请拆分文档"
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                return "Word 文件含有重复内部文件，无法可靠读取"
            if sum(entry.file_size for entry in entries) > MAX_UNPACKED_BYTES:
                return "Word 文件解压后超过大小上限，请拆分文档"
            roots = {}
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                    return "Word 文件内部路径无效"
                if entry.flag_bits & 1:
                    return "不支持加密的 Word 文件，请解密后重新上传"
                if "vbaproject" in entry.filename.lower():
                    return "不支持带宏的 Word 文件，请另存为普通 .docx"
                is_xml = entry.filename.endswith((".xml", ".rels"))
                limit = MAX_XML_BYTES if is_xml else MAX_UNPACKED_BYTES
                if entry.file_size > limit:
                    return "Word 文件内部内容超过大小上限，请拆分文档"
                # 读取会检查 CRC，不能只凭 ZIP 文件头认可文件真实类型。
                with archive.open(entry) as member:
                    raw = member.read(limit + 1)
                if len(raw) > limit:
                    return "Word 文件内部内容超过大小上限，请拆分文档"
                if is_xml:
                    if b"<!DOCTYPE" in raw.replace(b"\x00", b"").upper():
                        return "Word 文件包含不支持的 XML 声明"
                    roots[entry.filename] = ET.fromstring(raw)
            types = roots.get("[Content_Types].xml")
            relationships = roots.get("_rels/.rels")
            document = roots.get("word/document.xml")
            # 三个部件缺一不可：只有 ZIP + XML 不代表是 Word 文档。
            if types is None or relationships is None or document is None:
                return "文件不是有效的 .docx，缺少 Word 文档结构"
            main = [
                item
                for item in types
                if item.attrib.get("PartName") == "/word/document.xml"
                and item.attrib.get("ContentType") == _MAIN_TYPE
            ]
            targets = [
                item
                for item in relationships
                if item.attrib.get("Type", "").endswith("/officeDocument")
            ]
            # 只接受唯一主文档、Internal 关系、w:document 根元素且带 w:body 的普通文档。
            if (
                len(main) != 1
                or len(targets) != 1
                or targets[0].attrib.get("Target", "").lstrip("/") != "word/document.xml"
                or targets[0].attrib.get("TargetMode", "Internal") != "Internal"
                or document.tag != f"{{{WORD_NS}}}document"
                or document.find(f"{{{WORD_NS}}}body") is None
            ):
                return "文件不是受支持的普通 .docx，请在 Word 中重新另存"
    except (
        zipfile.BadZipFile,
        ET.ParseError,
        OSError,
        ValueError,
        RuntimeError,
        NotImplementedError,
    ):
        return "Word 文件损坏或格式不受支持，请重新另存为 .docx"
    return None
