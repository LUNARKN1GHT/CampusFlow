# V003：运行中删除资料的真实队列复验

日期：2026-10-18。执行与脚本作者：金嘉睿。Issue #139，父任务 #17。

**结论：两种真实队列竞态及迟到重复消息复验通过，待非作者审核验收证据。**
本任务不改变生产代码，补齐已有单元/集成测试之外的独立 Linux Worker 证据。

## 运行环境

- Windows 宿主机；Docker Desktop Linux 容器运行生产 `SafeWorker`（RQ 的分叉进程模型）。
- PostgreSQL 17 + pgvector；独立空库 `campusflow_v003_verified_test`，从零迁移到 `p001_planning_drafts`。
- 本轮专用 Redis 7 容器、随机本机端口及独立 Docker 网络。结束后自动删除本轮容器和网络，未消费现有业务队列，也没有清理业务库。
- Worker 镜像由当前父分支 `aac965d` 的 `backend/Dockerfile` 构建。镜像 ID：`fbdad26e1a2d9bcbbea3c8be68a8f0f6f71c55714be734db5a2511834bfb758a`。
- 宿主 Python 3.12.14，容器 Python 3.12.15；安装使用仓库锁文件。
- 两份本地生成的双页纯文本 PDF，不含真实课程信息；PDF 使用真实解析器，向量与回答使用确定性替身，模型费用为零。

## 如何触发真正的并发

新增 [verify_deletion_race.py](../../scripts/verify_deletion_race.py)。它不依靠“等待几秒后大概正在处理”的假设：

1. Worker 进入解析调用或第二块向量调用后，通过 Redis 发出“已到达”信号并等待。
2. 主进程先核对数据库作业确为 `running`，再通过受会话保护的删除预览和确认接口删除资料。
3. 删除接口返回 204 后，才发送放行信号，让仍在运行的后台调用继续返回结果。
4. 等待真实 Worker 退出，并检查 RQ 的失败记录，避免把“Worker 退出码为零、实际作业失败”误当成功。
5. 检查 API、实际数据库行和私有原文件，而不只检查页面文案。

握手最长等待 60 秒；超时立即判为失败。脚本不加载 `.env`，拒绝业务库和非空测试库，不会自行清表；使用 `python -O` 关闭断言时也会拒绝运行。

## 验收结果

| 场景 | 实际检查 | 结果 |
| --- | --- | --- |
| 原文件已读入、PDF 解析未返回时删除 | 删除返回 204，放行解析后没有重新创建版本、片段、作业、候选或索引；原文件下载为 404，文件实际不存在 | 通过 |
| 第一块向量已保存、第二块仍在计算时删除 | 删除前确认已有 1 个成功块、作业 `running`；删除后放行，清单和向量没有被迟到结果恢复 | 通过 |
| 保留已确认任务 | 通过候选保存用例、核对接口和转换接口创建正式任务；删除前设为进行中；删除后任务仍可读取、进度与进度历史保持，来源状态为 `invalidated` | 通过 |
| 旧回答中的原文引用 | 删除前从真实问答接口生成会话记录，引用有效；删除后重读同一会话，全部引用 `valid=false`、`status=source_deleted` | 通过 |
| 删除后重新检索 | 问答返回 `insufficient=true`，不返回已删原文引用 | 通过 |
| 删除后的迟到重复消息 | 向真实队列重新投递旧解析和索引作业编号，再启动两个 Worker；RQ 消息处理结束，但资料及派生内容仍为零 | 通过 |

候选生成环节直接调用已有保存用例，以固定证据建立测试事项；它不测试 AI 提取效果。API 使用 `TestClient` 执行真实路由与会话校验，不是浏览器操作，也不宣称完成页面易用性验收。

成功输出摘要：

```json
{
  "cases": [
    {"scenario": "delete_during_parse", "passed": true},
    {
      "scenario": "delete_during_index",
      "passed": true,
      "task_progress_preserved": true,
      "old_citations_invalid": true,
      "late_messages_finished_without_resurrection": true
    }
  ],
  "queue": "real Redis/RQ",
  "models": "fake, zero cost"
}
```

## 复验步骤

先准备一个**新的、空的、名字以 `_test` 结尾**的 PostgreSQL 数据库。脚本会迁移表结构，但不会创建或删除数据库；重复运行时换一个空测试库，避免覆盖上一次证据。

仓库根目录构建专用镜像：

```powershell
docker build -t campusflow-v003-worker:local backend
```

在 `backend/` 执行（地址替换为本次专用空库）：

```powershell
$env:CAMPUSFLOW_DATABASE_URL='postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_v003_new_test'
uv run --locked python ../scripts/verify_deletion_race.py
```

Docker 需能拉取或已缓存 `redis:7`。Worker 通过 `host.docker.internal` 连接宿主数据库；脚本针对现有 Windows + Docker 开发环境编写。数据库需允许该连接并具备迁移所需权限。失败时返回非零退出码，临时容器仍会清理；测试数据库保留现场，可核对具体失败步骤。

## 其他检查与范围限制

- 前端类型检查和构建通过；未修改前端或生产处理代码。
- 后端全量回归：`1306 passed`（111.22 秒，4 个既有警告），使用独立库 `campusflow_v003_test`。
- `ruff check . ../scripts/verify_deletion_race.py`、`ruff format --check . ../scripts/verify_deletion_race.py`、`git diff --check` 通过。
- 本证据只覆盖上述可重复的两处并发边界及迟到消息，不承诺穷尽所有操作系统、网络故障或任意并发交错。
- 不涉及真实模型的语义准确率、模型成本评测、浏览器无障碍或 M5 整体验收；这些仍按 Q019、V001、V004、V005 等任务记录。
