# X002 模型生成接口与结构化校验

任务：[X002 #109](https://github.com/LUNARKN1GHT/CampusFlow/issues/109)。作者：金嘉睿（24151735）。父 Issue：#11。前置：X001 #108（PR #355）、D013 #95。

## 交付范围

用户选择智谱与 `glm-4-flash-250414`，复用现有密钥，文字型号独立配置。本项提供可替换生成接口与受控应用入口，不增加安装包，不修改数据库，不增加提取 HTTP 接口或自动排队任务。

- `application/ports/generation.py`：TextGenerator 接口，真实与测试模型返回同样的原始字符串。
- `application/candidate_generation.py`：ExtractionSpec 明确事项类型、所有字段、文字/整数类型、是否允许缺失和允许出处。后续 X003–X005 定义实际业务字段；这里不提前固化它们。应用通过 Pydantic 严格检查格式，再复用纯领域校验。
- `generate_for_version`：先检查授权空间、归档状态与指定原文版本，再发送该版本片段；适用范围来自资料记录，模型不能指定空间、课程或班级。
- `infrastructure/llm/text.py`：标准库 HTTP 实现，固定智谱 HTTPS 地址，禁止跟随重定向；没有工具调用能力，不输出密钥、原文或供应商错误正文。
- `workers/generation.py`：显式装配入口，无启动副作用，配置缺密钥时报错。

## 数据契约与失败处理

请求使用系统消息保存应用控制的任务定义和 JSON 结构，用户消息只装原文片段编号与文字。导入内容中的命令按资料处理，不作为权限或系统操作指令。此处基础检查不代表 X009 的专门安全样本验收已完成。

模型必须返回 `{"items": [...]}`。每项包含 `kind`、`fields`、可选 `review_reasons`；字段包含 `name`、`value`、`origin`、`evidence` 和必要的 `explanation` / `missing_reason`。禁止额外属性、遗漏字段、重复字段、重复 JSON 键、非有限数字、类型自动转换和代码围栏。无匹配事项可以返回空数组；缺失字段用 null 和原因，不接受空白文字代替缺失值。

出处只允许契约指定的 document/inferred/suggestion，禁止模型冒充 user。有值的原文/推断字段需要有效证据，推断和建议需要说明。片段编号必须在本次提供的集合中，摘录必须逐字出现在对应片段。任何一项失败都不返回部分结果。生成入口不写库；后续用例显式调用 X001 `save_candidates` 再检查来源并保存，候选保持 pending，不创建正式任务。

每次限 1–100 个同版本非空片段，完整请求和输出正文各限 256 KiB；HTTP 响应最多读取 1 MiB + 1 字节以检测超限。固定输出 token 上限默认 4096，可配置至 8192。HTTP 408/429/5xx 和连接/读取失败标记可重试，其他 HTTP 拒绝、非法结构、截断或工具调用结果拒绝接受；适配器自身只调用一次，后续队列统一控制重试。

合法编号和真实摘录只能证明引用存在，不能证明提取结论正确。语义支持程度、漏提取及真实模型质量由 X010 单独评测；相对日期换算、范围歧义与来源冲突交给后续任务，不能将当前测试通过等同于这些能力完成。

## 验证

专项测试位于 `tests/test_candidate_generation.py`、`tests/test_text_generation_adapter.py`：合法/非法字段、缺失原因、推断出处、伪造引用、无效 JSON、跨空间/版本、归档、无部分保存、HTTP 错误分类、响应截断、禁止重定向、无工具调用、密钥缺失与模型配置。数据库测试使用独立 PostgreSQL `campusflow_x001_test`；HTTP 均用测试替身，不联网，不产生模型调用费用。

从 backend 运行（先设置 `CAMPUSFLOW_TEST_DATABASE_URL` 为独立测试库）：

```powershell
uv run --locked pytest tests/test_candidate_generation.py tests/test_text_generation_adapter.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

2026-10-13 本地验证：完整后端 **483 passed**（其中本项新增 77 项）；Ruff check、format --check、前端类型检查与构建、git diff --check 均通过。前端首次构建受沙箱子进程权限限制，放开构建权限重跑成功。既有依赖弃用及 integration 标记警告仍存在，不影响本轮通过。真实智谱调用和提取准确率尚未验证。

## 官方接口依据

2026-10-13 查阅：[GLM-4-Flash-250414](https://docs.bigmodel.cn/cn/guide/models/free/glm-4-flash-250414)、[结构化输出](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)。使用 `response_format={"type":"json_object"}` 并在提示词中明确输出结构；服务端 JSON 模式不能代替本地字段与证据检查。具体额度与可用性以账号实际状态为准。
