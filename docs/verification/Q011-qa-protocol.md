# Q011–Q013、Q015 问答用例与协议

日期：2026-10-18

## 接口

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/v1/qa/query` | 单次问答；提交问题与学期/课程/教学班范围 |
| `POST /api/v1/qa/intent` | 识别 query / planning / conflict_check 意图 |
| `POST /api/v1/qa/conversations` | 创建有范围会话 |
| `POST /api/v1/qa/conversations/{id}/messages` | 在会话范围内追问 |
| `GET /api/v1/qa/conversations/{id}` | 读取会话并重新核对引用来源 |

## Q011 协议与范围

- 请求体禁止额外字段；学期、课程、教学班由 `RetrievalScope` 与服务端授权用例再次校验，课程必须属于所选学期，课程班级冲突返回 400。
- 响应把 `answer` 与 `citations` 分开；引用含资料、版本号、块、来源片段、页码/段落与原文摘录。
- 读取资料的事务在调用向量/生成模型前提交，避免持锁等待外部服务。
- 模型失败按 Q009 契约返回：临时故障 503、请求拒绝/输出无效 502，均带固定中文文案，不返回答案或引用。

## Q012 资料不足与部分覆盖

- 没有候选时不调用模型，直接返回 `insufficient=true` 与固定文案“现有资料中没有找到足以回答该问题的依据”，不表述为“没有作业”。
- 部分解析/索引失败通过 `coverage` 返回成功/失败/跳过块数与解析未覆盖数量；界面与回答同时可见。
- 尚未解析或索引的资料以 `unindexed` 提示列出，明确“未参与本次回答”。

## Q013 多资料对照与冲突

- `comparisons` 按资料并列展示引用片段与适用范围。
- 同一类截止/考试日期在不同资料中不一致时返回 `conflicts`，`requires_review=true`，同时列出全部日期；没有任何“已选定生效日期”字段，不按导入时间自动择一。
- 回答中的不确定项并入 `uncertainties`，提示核对生效版本。

## Q015 意图协议

- 模型只能输出固定枚举与参数白名单；出现工具名、命令或额外字段即拒绝。
- 规划、冲突检查尚未上线时返回 `available=false`、`executed=false` 与能力边界文案；生成文本不会变成任意工具执行。

## 验收证据

```bash
cd backend && uv run --locked pytest tests/test_qa_api.py tests/test_qa_acceptance.py -q
```

覆盖：答案/引用分离、范围参数校验、越界引用拒绝、无候选不调用模型、部分覆盖提示、冲突并列、意图边界与工具注入拒绝。
