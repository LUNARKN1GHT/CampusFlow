# A018 任务清单导出

作者：李亦凡；任务 #209；父任务 #22。前置 #205（A017）。

## 范围

- 只导出请求中显式给出的 `task_ids`：空列表、缺失或跨空间 ID 直接拒绝，不回退为整空间导出。
- 范围、字段与隐私边界按 `docs/export-scope.md` §1–§3、§5。
- CSV 使用 UTF-8 BOM、CRLF、RFC 4180 引号；表头固定，空值留空。
- 下载必须携带预览返回的 `scope_fingerprint`；任务修订、范围或选项变化后返回 409 并要求重新预览。
- 不导出描述、截止备注、原文／引文、教师与教学班；来源提示只在 `include_source_hints=true` 且来源仍可核对时写入，最多到标题、版本号与定位。

## 验证

```text
backend$ uv run --locked ruff check .
All checks passed!
backend$ uv run --locked ruff format --check .
... clean
backend$ uv run --locked pytest -q tests/test_exports.py
17 passed
```

覆盖：显式范围与去重、仅日期／缺日期精度、特殊字符往返、已取消任务排除与警告、来源失效与隐私字段、指纹失效 409、空范围与计划模式拒绝。

## 未覆盖与限制

- 计划模式与 ICS 由 A019 接入；计划模式 CSV 仍未提供。
- 导出不在服务端留存文件或历史记录。
