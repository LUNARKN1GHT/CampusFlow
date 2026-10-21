# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder 规格（P002a，macOS arm64 为验收平台）。

只做 one-folder：桌面外壳把整个目录随包分发，启动时不再解压到临时目录。
迁移脚本与 alembic.ini 作为显式 datas 放在 ``_MEIPASS/migrations`` 与
``_MEIPASS/alembic.ini``，由 desktop/sidecar_entry.py 按绝对路径解析。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
BACKEND = ROOT / "backend"

datas = [
    # 迁移脚本、script.py.mako 与版本目录必须整体保留，Alembic 运行时按文件扫描。
    (str(BACKEND / "migrations"), "migrations"),
    (str(BACKEND / "alembic.ini"), "."),
    # python-docx 的默认模板；缺失时 Word 解析在运行期才报错。
    *collect_data_files("docx"),
    # pypdfium2_raw 的 libpdfium.dylib 与 version.json；bindings.py 按文件位置加载 dylib。
    *collect_data_files("pypdfium2_raw"),
]
binaries = collect_dynamic_libs("pypdfium2_raw")

hiddenimports = [
    # Alembic 以 exec 方式执行 env.py，静态分析看不到其中的 campusflow 导入。
    "campusflow.core.config",
    "campusflow.infrastructure.db.base",
    "campusflow.infrastructure.db.models",
    # SQLAlchemy 按连接 URL 动态选择 psycopg 3 方言。
    "sqlalchemy.dialects.postgresql.psycopg",
    # psycopg[binary] 在运行时按实现选择 C 扩展。
    "psycopg_binary._psycopg",
    "psycopg_binary._uuid",
    "psycopg_binary.pq",
    # FastAPI 表单/文件路由依赖 multipart；新旧包名都保留。
    "multipart",
    "python_multipart",
    # pypdfium2 通过 ctypes 加载绑定，避免只收集顶层包而漏掉 raw 模块。
    "pypdfium2",
    "pypdfium2_raw",
    "pypdfium2_raw.bindings",
    "pypdfium2_raw.version",
    "h11",
]
# uvicorn/rq/alembic 都有按配置动态导入的子模块（协议实现、执行器、命令）。
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("rq")
hiddenimports += collect_submodules("alembic")
# P006 统一 CLI：子命令按需导入 sidecar 模块、桌面 Worker 与队列装配。
hiddenimports += collect_submodules("campusflow.sidecar")
hiddenimports += collect_submodules("campusflow.workers")
hiddenimports += collect_submodules("campusflow.infrastructure.queue")

a = Analysis(
    [str(ROOT / "desktop" / "sidecar_entry.py")],
    pathex=[str(BACKEND / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "ruff"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="campusflow-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="campusflow-sidecar",
)
