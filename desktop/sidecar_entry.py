"""PyInstaller sidecar 入口：委托给 `campusflow.sidecar.cli`（P006）。

P002a 最初在此维护独立的 serve/migrate/worker 实现；P006 的统一 CLI 合入后
只保留这一层转发，避免两套命令语义与退出码。PyInstaller 仍以本文件作为
Analysis 入口，实际子命令、日志与脱敏由 sidecar 包负责。
"""

from __future__ import annotations

import sys

from campusflow.sidecar.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
