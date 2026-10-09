"""本地私有目录文件存储（D002）。

存储键格式：`{年}/{月}/{随机标识}.{扩展名}`，与用户文件名完全无关。
根目录为后端私有目录（默认 backend 下 data/materials，不进 Git），
本实现不提供任何公开 URL；读取必须走带授权的业务接口。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path


class StorageKeyError(ValueError):
    """存储键非法（空、绝对路径或试图越出根目录）。"""


class LocalFileStorage:
    def __init__(self, root_dir: Path | str) -> None:
        self._root = Path(root_dir).resolve()

    def _resolve(self, storage_key: str) -> Path:
        """把存储键解析为根目录内的路径；越界即拒绝。

        反斜杠在所有平台上都视为路径分隔符，保证 Windows 与 Linux 行为一致。
        """
        if not storage_key:
            raise StorageKeyError(f"非法存储键：{storage_key!r}")
        normalized = storage_key.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized:
            raise StorageKeyError(f"非法存储键：{storage_key!r}")
        candidate = (self._root / normalized).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise StorageKeyError(f"非法存储键：{storage_key!r}")
        return candidate

    def save(self, content: bytes, extension: str) -> str:
        now = datetime.now()
        key = f"{now:%Y}/{now:%m}/{uuid.uuid4().hex}.{extension}"
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return key

    def read(self, storage_key: str) -> bytes:
        return self._resolve(storage_key).read_bytes()

    def delete(self, storage_key: str) -> bool:
        path = self._resolve(storage_key)
        if not path.exists():
            return False
        path.unlink()
        return True
