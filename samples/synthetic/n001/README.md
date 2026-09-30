# N001 样本：Word 与公开网页验收样本

全部为合成内容（不含真实课程或个人资料），许可为 `project-internal`，用途登记见 `samples/registry.csv`。

## 样本清单

| 文件 | 覆盖场景 | 人工预期 |
| --- | --- | --- |
| `word-notice-paragraph.docx` | Word 段落与编号列表：作业通知的提交方式、截止时间、适用对象 | `expected.json` 中 CF-DOC-001 |
| `word-notice-table.docx` | Word 表格：考试安排的行列定位 | `expected.json` 中 CF-DOC-002 |
| `public-notice.html` | 公开可访问网页：教务公告的正文与发布信息 | `expected.json` 中 CF-WEB-001 |

两个 Word 样本由 `generate.py` 生成（依赖 python-docx）：

```bash
cd backend && uv run --with python-docx ../samples/synthetic/n001/generate.py
```

## 无文件场景（在 expected.json 的 scenarios 中登记）

- `web-inaccessible`：公开网页无法访问（连接失败或 404），预期导入失败并展示原因与重试入口。
- `web-requires-login`：页面需要登录（重定向到登录页），预期标记为需要登录并允许改用上传或粘贴原文，不抓取登录后内容。

两个场景不提供样本文件，由测试代码以模拟响应或本地不可达地址构造。
