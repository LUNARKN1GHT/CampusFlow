"""Windows DPAPI 凭据文件（P007）。

用 ctypes 调用 CryptProtectData／CryptUnprotectData，不新增依赖；密文绑定当前
Windows 用户，文件被复制到其它用户或机器上无法解密。0600 在 Windows 上只影响
只读位，访问控制实际由 DPAPI 提供。本实现尚未在真实 Windows 机器上验证。
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

BACKEND_NAME = "windows-dpapi"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


class DpapiError(RuntimeError):
    """DPAPI 加解密失败；消息只含错误码，不含凭据内容。"""


def _crypt32() -> object:
    if sys.platform != "win32":  # pragma: no cover - 仅 Windows 可达
        raise DpapiError("DPAPI 只在 Windows 上可用")
    return ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]


def _kernel32() -> object:
    return ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _protect(data: bytes) -> bytes:
    blob_in, _buffer = _blob(data)
    blob_out = _DataBlob()
    ok = _crypt32().CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise DpapiError(f"DPAPI 加密失败（错误码 {ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32().LocalFree(blob_out.pbData)  # type: ignore[attr-defined]


def _unprotect(data: bytes) -> bytes:
    blob_in, _buffer = _blob(data)
    blob_out = _DataBlob()
    ok = _crypt32().CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise DpapiError(f"DPAPI 解密失败（错误码 {ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32().LocalFree(blob_out.pbData)  # type: ignore[attr-defined]


class WindowsDpapiSecretStore:
    """JSON 文件保存 DPAPI 密文，每个账户一条。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @staticmethod
    def is_available() -> bool:
        return sys.platform == "win32"

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
        except json.JSONDecodeError as exc:
            raise DpapiError("凭据文件损坏，无法解析") from exc
        if not isinstance(data, dict):
            raise DpapiError("凭据文件格式无效")
        return {key: value for key, value in data.items() if isinstance(value, str)}

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

    def get(self, account: str) -> str | None:
        encoded = self._load().get(account)
        if encoded is None:
            return None
        return _unprotect(base64.b64decode(encoded)).decode("utf-8")

    def set(self, account: str, value: str) -> None:
        data = self._load()
        data[account] = base64.b64encode(_protect(value.encode("utf-8"))).decode("ascii")
        self._save(data)

    def delete(self, account: str) -> bool:
        data = self._load()
        if account not in data:
            return False
        del data[account]
        self._save(data)
        return True
