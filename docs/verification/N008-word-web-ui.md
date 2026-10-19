# N008 接通 Word 与网页导入界面和作业流程

作者：李亦凡；任务 #167；父任务 #18。依赖 N003 #153、N006 #159、N007 #163、J006 #102、D019 #103。

## 已实现的界面行为

资料库沿用同一套导入面板和资料生命周期：

- 文件入口接受 PDF、Word（DOCX）、PNG、JPG/JPEG，并提示旧版 DOC、DOCM 等先另存为普通 DOCX；DOCX 的格式、大小和结构校验仍由 N002/N003 的后端逻辑执行。
- 公开网页入口由 N007 提供，抓取成功后在资料列表显示为“网页”来源并绑定解析作业；失败时进入粘贴或上传替代路径。
- Word 与网页导入都通过既有 `POST /api/v1/materials/upload`、`POST /api/v1/materials/web` 建立资料、版本和 `parse` 作业，随后复用资料列表的处理状态、重试、失败原因、部分成功覆盖范围和原文定位，不新增第二套解析或引用链路。
- 多文件上传逐项展示结果，并汇总本次成功、重复与失败数量；单个文件格式失败只显示该项错误，不计入成功，也不影响同批其它文件建档。

## 验收条件对应

| 验收条件 | 证据 |
| --- | --- |
| 两种输入可进入核对及问答 | Word 与网页都生成同一种资料/版本/片段记录，核对与问答读取相同来源范围；N003/N006 的后端测试覆盖 DOCX 与网页片段生成，Q004/Q005/Q018 覆盖索引、范围过滤与带引用问答。端到端链路验收由 N009 #172 负责。 |
| 格式失败不会标记全部成功 | 上传结果按文件返回 `material` / `error` / `duplicate_of`，界面汇总“成功/重复/失败”数量并逐项显示失败原因；Playwright 用无效 DOCX + 有效 DOCX 同批上传验证。 |

## 验证

```text
frontend$ npm run build
✓ vue-tsc --noEmit
✓ vite build

frontend$ npx playwright test tests/e2e/materials.spec.ts -g "M6 Word"
1 passed

frontend$ npx playwright test tests/e2e/materials.spec.ts -g "M6 网页不可访问"
1 passed

backend$ uv run --locked pytest tests/test_word_parser.py tests/test_word_upload.py tests/test_web_parser.py tests/test_web_import.py -q
passed
```

Playwright 使用真实后端与 N001 合成 DOCX 样本；未调用外部模型或真实外站。

## 未覆盖与限制

- 旧版 DOC/DOCM 不转换执行，只提示另存为 DOCX；宏文档按 N002 拒绝。
- Word 中的图片、修订和正文外内容按 N003 报告未覆盖；网页登录门户和反爬识别范围按 N006/N007 的限制。
- 未新增自动提取流程：导入后的核对候选仍按既有 X 系列入口处理；扩展输入到引用问答的完整验收留给 N009。
