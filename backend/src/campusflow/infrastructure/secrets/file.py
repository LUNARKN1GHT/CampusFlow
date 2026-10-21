"""0600 本地凭据文件（P007 兜底实现）。

只在没有系统密钥链时使用：明文落在数据目录，属于受控的降级方案，
调用方必须把降级原因写进报告并提示用户。文件按 0600 创建并以原子替换更新。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

BACKEND_NAME = "file"


class FileSecretStore:
    """`secrets.json`：账户名到口令的映射，权限 0600。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def backend(self) -> str:
        return BACKEND_NAME

    def _load(self) -> dict[str, str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 损坏时按缺失处理，由调用方提示凭据丢失；不猜测、不覆盖其它条目。
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            key: value
            for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def _save(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".secrets-", suffix=".tmp"
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, sort_keys=True)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self._path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        # Windows 上 os.replace 后仍显式收紧一次，不依赖临时文件权限继承。
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def get(self, account: str) -> str | None:
        return self._load().get(account)

    def set(self, account: str, value: str) -> None:
        data = self._load()
        data[account] = value
        self._save(data)

    def delete(self, account: str) -> bool:
        data = self._load()
        if account not in data:
            return False
        del data[account]
        self._save(data)
        return True
