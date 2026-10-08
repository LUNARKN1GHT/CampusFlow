"""D002 测试：私有原文件存储适配器。

验收：用户文件名不能决定磁盘路径；存储键越界访问被拒绝；
扩展名走白名单；读写删闭环。
"""

import re

import pytest

from campusflow.domain.storage import ALLOWED_EXTENSIONS, sanitize_extension
from campusflow.infrastructure.storage.local import LocalFileStorage, StorageKeyError


def test_storage_key_ignores_user_filename(tmp_path) -> None:
    """存储键只含年月与随机标识，不出现任何用户文件名信息（D002 验收）。"""
    storage = LocalFileStorage(tmp_path)
    key = storage.save(b"hello", sanitize_extension("张老师发的作业要求（绝密）.pdf"))
    assert "张老师" not in key
    assert "作业" not in key
    assert re.fullmatch(r"\d{4}/\d{2}/[0-9a-f]{32}\.pdf", key)


def test_save_read_delete_roundtrip(tmp_path) -> None:
    storage = LocalFileStorage(tmp_path)
    key = storage.save(b"PDF-bytes", "pdf")
    assert (tmp_path / key).exists()
    assert storage.read(key) == b"PDF-bytes"
    assert storage.delete(key) is True
    assert storage.delete(key) is False
    with pytest.raises(FileNotFoundError):
        storage.read(key)


def test_path_traversal_is_rejected(tmp_path) -> None:
    """构造越界存储键读取/删除必须被拒绝，不能逃出私有根目录（D002 验收）。"""
    storage = LocalFileStorage(tmp_path / "private")
    (tmp_path / "secret.txt").write_text("外面的文件")
    for bad_key in ("../secret.txt", "../../etc/passwd", "/etc/passwd", "C:/Windows/x", "..\\up"):
        with pytest.raises(StorageKeyError):
            storage.read(bad_key)
        with pytest.raises(StorageKeyError):
            storage.delete(bad_key)


def test_sanitize_extension_whitelist() -> None:
    assert sanitize_extension("作业.PDF") == "pdf"
    assert sanitize_extension("png") == "png"
    assert sanitize_extension(".JPG") == "jpg"
    for ext in ALLOWED_EXTENSIONS:
        assert sanitize_extension(ext) == ext
    with pytest.raises(ValueError):
        sanitize_extension("病毒.exe")
    with pytest.raises(ValueError):
        sanitize_extension("script.sh")


def test_keys_are_unique(tmp_path) -> None:
    storage = LocalFileStorage(tmp_path)
    keys = {storage.save(b"x", "txt") for _ in range(20)}
    assert len(keys) == 20


def test_storage_has_no_public_url_interface(tmp_path) -> None:
    """存储适配器不提供公开访问地址（D002 验收：文件不能绕过授权公开访问）。"""
    storage = LocalFileStorage(tmp_path)
    public_attrs = [
        name for name in dir(storage) if "url" in name.lower() or "public" in name.lower()
    ]
    assert public_attrs == []
