"""sidecar 结构化日志：一行一个 JSON 事件，默认写 stderr，可另存日志文件。

事件字段固定为 timestamp／level／command／event，其余调用方字段按需附加。
所有字符串都先脱敏：连接串中的口令、`?password=` 查询参数和敏感键名一律
替换成 `***`，避免口令随诊断或备份日志扩散到终端、文件或 Electron 管道。
"""

from __future__ import annotations

import json
import re
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

# 键名包含以下片段时整值替换，不再尝试判断内容是否真的敏感
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "cookie",
    "authorization",
)
# `scheme://user:secret@host` 只保留 scheme 与用户名，口令替换为 ***
_URL_USERINFO = re.compile(r"(?P<prefix>[A-Za-z][A-Za-z0-9+.\-]*://[^:/@\s]*):[^@\s]*@")
# `?password=...&x=1` 只替换参数值，保留参数名便于定位配置项
_QUERY_SECRET = re.compile(
    r"(?i)((?:password|passwd|pwd|secret|token|api_key|apikey|key)=)[^&\s\"'<>]+"
)

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}


def redact_text(value: str) -> str:
    """把连接串与查询参数中的口令替换为 `***`；其它内容保持不变。"""
    value = _URL_USERINFO.sub(r"\g<prefix>:***@", value)
    return _QUERY_SECRET.sub(r"\1***", value)


def _is_sensitive_key(key: object) -> bool:
    name = str(key).lower()
    return any(part in name for part in _SENSITIVE_KEY_PARTS)


def redact_value(value: Any) -> Any:
    """递归脱敏任意可 JSON 序列化的值。"""
    if isinstance(value, dict):
        return {
            key: "***" if _is_sensitive_key(key) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def scrub_text(text: str, secrets: list[str | None]) -> str:
    """删除文本中出现的具体密钥原文；用于最终输出前的兜底检查。"""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return redact_text(text)


def build_log_file(path: str | Path) -> TextIO:
    """按需创建日志文件与其父目录；失败由调用方按运行失败处理。"""
    log_path = Path(path).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path.open("a", encoding="utf-8")


class EventLogger:
    """一个命令一个实例；写 stderr 与可选日志文件，线程安全且不抛编码错误。"""

    def __init__(
        self,
        command: str,
        *,
        stream: TextIO | None = None,
        log_file: TextIO | None = None,
        level: str = "info",
    ) -> None:
        self.command = command
        self._stream = stream if stream is not None else sys.stderr
        self._log_file = log_file
        self._threshold = _LEVELS.get(level, 20)
        self._lock = threading.Lock()

    def event(self, event: str, level: str = "info", **fields: Any) -> dict[str, Any]:
        """输出一个事件；字段先脱敏再序列化，返回值便于测试断言。"""
        if _LEVELS.get(level, 20) < self._threshold:
            return {}
        record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": level,
            "command": self.command,
            "event": event,
        }
        for key, value in fields.items():
            record[key] = redact_value(value)
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            for stream in (self._stream, self._log_file):
                if stream is None:
                    continue
                try:
                    stream.write(line + "\n")
                    stream.flush()
                except (OSError, ValueError):  # 终端关闭或管道断开时不影响主流程
                    continue
        return record

    def info(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.event(event, level="info", **fields)

    def warning(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.event(event, level="warning", **fields)

    def error(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.event(event, level="error", **fields)

    def close(self) -> None:
        if self._log_file is None:
            return
        try:
            self._log_file.close()
        except OSError:
            pass
        finally:
            self._log_file = None
