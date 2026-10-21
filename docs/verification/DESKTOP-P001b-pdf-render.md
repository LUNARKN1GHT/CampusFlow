# P001b PDF 渲染替换：PyMuPDF → pypdfium2

作者：李亦凡；任务 [#480](https://github.com/LUNARKN1GHT/CampusFlow/issues/480)；父任务 [#467](https://github.com/LUNARKN1GHT/CampusFlow/issues/467)。日期：2026-10-21。

范围：移除 AGPL 的 `pymupdf`，用 `pypdfium2` + `Pillow` 保持“扫描 PDF 按页渲染为 150 dpi PNG”的既有行为，并补齐像素尺寸、扫描页、页码越界和损坏文件的测试。许可结论见 [DESKTOP-P001a-license-audit.md](./DESKTOP-P001a-license-audit.md)。

## 依赖变更

`backend/pyproject.toml`：

- 移除 `pymupdf>=1.24,<2.0`。
- 新增 `pypdfium2>=5.0,<6.0`，锁定为 `5.14.0`（wheel 内置 PDFium `156.0.8076.0`）。
- 新增 `pillow>=11,<14`，锁定为 `13.0.0`，只用于 PNG 编码。
- `backend/uv.lock` 由 `uv lock` 更新；`uv sync --locked` 后 `pymupdf` 已从环境移除。

## 实现

`backend/src/campusflow/infrastructure/parsers/pdf_render.py`：

- `render_pdf_page(content: bytes, page_no: int) -> bytes` 仍按 150 dpi 渲染；PDF 点值为 1/72 英寸，因此 `scale = 150 / 72`。
- 只使用内存：`PdfDocument(content)` 读取字节，`PdfBitmap.to_pil()` + `BytesIO` 编码 PNG，不写临时文件。
- 页码从 1 开始；越界抛 `ValueError("页码越界：…（共 N 页）")`，与旧实现文案一致。
- 空文件、损坏文件或 PDFium 渲染失败统一转成 `ValueError`；`PdfWithVisionFallbackParser` 现有的 `except ValueError` 会把它记为单页失败，不抹掉其他页。
- 文档句柄在 `finally` 中关闭。

`backend/tests/test_pdf_vision_fallback.py`：

- 用 pypdf 直接构造“第 1 页文字、第 2 页纯图片”的合成 PDF，不再依赖 PyMuPDF。
- 保留原有文本页优先、扫描页回退、单页失败隔离、失败可重试语义和空白页测试。
- 新增 150 dpi 尺寸、扫描图片页、损坏/空 PDF 和回退解析器处理渲染错误的测试。

`scripts/`：

- 新增 `scripts/pdf_fixtures.py`，用 pypdf + Pillow 生成脚本所需的合成 PDF/PNG。
- `smoke_material_processing.py` 与 `m2_browser_fixtures.py` 不再导入 `pymupdf`；M2 的 `fixtures` 动作已实际运行并生成可被 `PdfDocumentParser` 提取的文本 PDF。

## 行为契约

| 输入 | 行为 |
| --- | --- |
| 有效 PDF，页码在范围内 | 返回 PNG 字节；像素尺寸按 150 dpi 计算（144×72 pt → 300×150 px） |
| 扫描页/纯图片页 | 正常渲染，调用方继续走视觉识别 |
| 页码越界 | `ValueError`，中文“页码越界” |
| 空文件、损坏 PDF | `ValueError`，中文“无法打开 PDF”；回退解析器按单页失败处理 |
| 任意输入 | 不写临时文件；不修改原文件；文档句柄关闭 |

## 验证

### 目标测试

```text
backend$ uv run pytest tests/test_pdf_vision_fallback.py -q
13 passed, 1 warning in 0.13s
```

### 全量测试

```text
backend$ uv run pytest
============ 1563 passed, 6 skipped, 4 warnings in 83.89s (0:01:23) ============
```

说明：全量测试使用本机 `campusflow_test` PostgreSQL。`tests/conftest.py` 的逐用例清库引擎在本轮补上了 `engine.dispose()`；此前每个用例新建的引擎会把空闲连接累积到 PostgreSQL 默认 `max_connections=100`，导致后段用例出现 “remaining connection slots are reserved” 错误。该修改只影响测试资源释放，不改变产品行为。

### 静态检查

```text
backend$ uv run ruff check .
All checks passed!
```

```text
$ rg -n "pymupdf|import fitz" backend/src backend/tests
（无输出，退出码 1）
$ rg -n "pymupdf|import fitz" scripts
（无输出，退出码 1）
```

### 输出尺寸与渲染耗时

在同一台 Apple Silicon macOS、Python 3.12.13 环境，用同一份合成 PDF 对比旧实现（PyMuPDF 1.28.2）和新实现（pypdfium2 5.14.0 / Pillow 13.0.0）。计时包含 PNG 编码，先预热 2 次，再取 12 次的中位数；这是单机一次测量，不作为统计结论。

| 页面 | 旧：PyMuPDF | 新：pypdfium2 | 中位耗时（旧 → 新） |
| --- | --- | --- | --- |
| Letter 文字页 612×792 pt | 1275×1650 px，14747 B | 1275×1651 px，18482 B | 22.1 ms → 12.3 ms |
| Letter 图片页 612×792 pt | 1275×1650 px，9745 B | 1275×1651 px，9788 B | 29.4 ms → 18.8 ms |
| 第二份 Letter 文字页 | 1275×1650 px，10605 B | 1275×1651 px，11502 B | 29.9 ms → 15.1 ms |
| 144×72 pt 小页 | 300×150 px | 300×150 px（空白页 499 B） | 未单独计时 |

PDFium 对像素尺寸使用向上取整：Letter 的 792 pt × 150/72 = 1650，但浮点结果为 1650.0000000000002，`ceil` 后是 1651；因此 Letter 输出比旧实现高 1 像素。144×72 pt 等能整除的页面完全一致。该差异不影响 150 dpi 的比例，也不影响 OCR 使用；如果后续需要与旧输出逐像素一致，需要在渲染后额外裁掉多出的 1 像素，本文不采用这种做法。

## 未覆盖与限制

- 只用合成 PDF 验证了页数、文字层、图片页和尺寸；没有真实扫描件/拍照件的识别准确率对比，也没有逐像素比较文字抗锯齿差异。
- 计时是本机单次测量，受 CPU、磁盘和后台任务影响；未做跨平台或大文件压力测试。
- 仅验证 macOS arm64 wheel；Windows/Linux 的 PDFium/Pillow wheel 版本和渲染结果需在对应构建机复验。
- PDFium 的安全更新依赖 pypdfium2 升级；后续升级时需重复本页测试和许可证 notice 复核。
- `scripts` 中的冒烟脚本只验证了 fixture 生成与文本解析路径；需要真实 PostgreSQL/Redis/Worker 的完整冒烟未在本次执行。
