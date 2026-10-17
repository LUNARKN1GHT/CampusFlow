# Q004 完成记录：向量索引自动流水线、失败恢复与检索加速

日期：2026-10-17。作者：金嘉睿。任务 #131，父任务 #13。

## 本轮选择（已由用户拍板）

| 决策 | 结论 |
| --- | --- |
| 向量调用方式 | 一块一次：每段文字单独调用模型，成功立刻保存 |
| 每块重试上限 | 3 次（按领取计数，崩溃也算一次） |
| 自动恢复扫描位置 | 后端 API 进程内，默认每 15 秒一次 |
| 检索加速索引 | HNSW，随数据库升级自动创建（余弦距离） |

用户此前已选择 pgvector 的 Python 包；Q002 PR #384、Q003 PR #385 已由队友合并，
J004 #90 已完成。本分支已同步父分支 `296e46f`。

## 一、零件层（本轮之前完成）

`domain/indexing.py` 是不依赖数据库、模型或队列的进度规则：

- 独立的块状态 pending/running/done/failed/skipped，不混用用户任务执行进度。
- 汇总以完整分块清单为分母；未写进度的块算 pending，不因缺记录而消失。
- 只有非空清单内全部块成功、解析覆盖元数据已验证且未覆盖数量为零，才允许
  `fully_searchable=True`。解析覆盖未知、失败、跳过均不能被掩盖；有成功但不完整为
  partial，零成功终态为 failed。
- `blocks_needing_work` 只挑选未处理和可重试失败块，排除已成功、正在处理、永久失败
  和跳过项；不修改输入记录。
- 重复清单、重复/矛盾进度或清单外记录报错，不能靠去重把矛盾悄悄吞掉。

`indexed_blocks` 表以配置编号 + 块编号为复合主键，保存来源版本、原片段、源指纹、
字符范围、文本、逐块状态和 `vector(1024)`。成功向量与 done 状态同事务保存；重复
登记不改既有进度，重复成功结果不覆盖首次成功。配置代次不同分别存储，删除来源级联
删除向量。

`index_manifests` 表把一份版本的全部片段、指纹、模型配置、分块设置与解析覆盖做成
快照；清单与初始块同事务保存，相同准备幂等。来源新增、删除、改写或覆盖信息变化后，
旧清单拒绝继续领取、写回或汇总。

领取在短事务里完成：同一块只有一个有效凭证，默认 900 秒；成功/失败写回都必须携带
有效凭证，过期凭证即使尚未被接替也不能写回。模型调用必须放在领取事务提交之后、
保存事务开始之前。

## 二、本轮完成：把零件接成自动运行的机器

### 1. 连接模型调用

- 新增 `application/ports/indexing.py`（`IndexedBlocks` / `IndexManifests` 协议）与
  `application/indexing.py::index_version`，流程严格遵循：
  1. `repos.jobs.claim(job_id)` 幂等领取，重复消息或已结束作业直接返回，**不调用模型**；
  2. 核对资料未删除、版本未被取代，命中则放弃写回并留痕；
  3. 注册/校验 `EmbeddingProfile`（配置不一致时不发网络请求）；
  4. `manifests.prepare(...)` 生成快照并登记块，与 `set_manifest` 同事务提交；
  5. 逐块短事务领取 → 提交 → **事务外**调用模型 → 新事务凭凭证保存；
  6. `summary(verify_snapshot=True)` 汇总，映射为 done / partial / failed。
- 失败码只写受控安全代码（`upstream_transient`、`invalid_response`、
  `source_unavailable`、`attempts_exhausted`、`lease_expired`），绝不写异常原文、
  密钥或正文。
- 每领取一块刷新 `heartbeat_at`，长作业不会被误判超时。
- 索引作业**不修改资料状态**：资料状态只代表解析结果。
- 性能：循环内部使用 `verify_snapshot=False` + 单条 `FOR UPDATE SKIP LOCKED LIMIT 1`
  领取，避免逐块重算快照导致 O(n²)。

### 2. 接入后台处理队列

- 新增独立索引队列 `campusflow-index`（解析队列仍为 `campusflow-parse`），稳定作业键
  `index-{job_id}`；`dispatch_job` 按 `stage` 选择投递目标，未知阶段记为不可重试失败。
- 解析成功（done 或 partial 且有片段）时在**同一事务**里 `create_or_requeue(embed)`：
  不存在则新建 pending；已结束则重置为 pending（已完成块不删，不重复调用模型）；
  pending/running 不动。数据库部分唯一索引
  `uq_processing_jobs_embed_version` 保证一份版本最多一个 embed 作业。
- **Outbox 语义**：Worker 只写数据库、不直接碰 Redis；投递由 API 恢复扫描完成，
  最长延迟一个扫描周期；Redis 不可用时记录留待下轮补投。
- Worker 入口 `SafeWorker([campusflow-parse, campusflow-index])`；
  `--demo` 同时启用假识别与假向量器（`DemoEmbedder`，sha256 确定性 1024 维向量，
  不联网、不花钱），并且只允许库名以 `_test` 结尾。

### 3. 失败与中断恢复

- 迁移 `q004_index_pipeline`：`indexed_blocks.attempts`（默认 0，非负约束）、
  `processing_jobs.manifest_id / heartbeat_at / auto_retry_count`、embed 部分唯一索引。
- 每块上限 3 次：领取计数（Worker 领取后崩溃也算一次），耗尽写
  `attempts_exhausted` 且不可重试。
- 块租约 900 秒；过期后同一块可被重新领取，旧凭证写回被拒绝。
- API 进程内恢复扫描 `run_recovery_sweep`，三步各自独立会话、独立 try/except：
  1. **投递补偿**：重投所有 pending 且未成功入队的作业（parse + embed）；
  2. **超时回收**（阶段感知）：parse 看 `started_at`；embed 看
     `COALESCE(heartbeat_at, started_at)`；只有 parse 回收才把资料置 FAILED；
  3. **自动重投**：embed 阶段 failed/partial 且可重试、自动重投次数 < 3 的作业，
     原子条件更新后重新投递。
- 启动时先执行一次扫描；`CAMPUSFLOW_RECOVERY_INTERVAL_SECONDS=15` 起后台循环
  （0 表示只扫描启动一次）。单轮失败只记日志，循环继续；关闭服务时任务被取消并等待。
- 达到自动重投上限后不再自动重试，保留人工 `POST /api/v1/jobs/{id}/retry`；
  人工重试只对 parse 阶段重置资料状态。

### 4. 检索加速索引

迁移 `q004_index_hnsw`：

```sql
CREATE INDEX ix_indexed_blocks_embedding_hnsw
ON indexed_blocks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

- 余弦距离，与 `domain/embeddings.py::cosine_similarity` 一致。
- 当前表规模小，在 Alembic 事务内普通建索引即可；将来表大时再改用 `CONCURRENTLY`
  （已在迁移注释中说明）。
- 不按 `profile_id` 建部分索引：profile id 运行时生成，迁移无法预知。
- ORM 同步声明该索引，避免以后 autogenerate 误删；`downgrade` 只删索引，不删
  `vector` 扩展。

### 5. 进度接口

`GET /api/v1/jobs/{job_id}/index-progress?include_blocks=false`：

- 复用 `get_job` 做空间归属校验（越权与不存在同为 404）；非 embed 作业返回 400；
- 尚无清单时 `manifest_id=null, summary=null`，前端显示“等待索引”；
- 返回汇总（expected/indexed/pending/running/failed/skipped/retryable_failed/
  fully_searchable 与覆盖信息）和可选的逐块列表；
- 块列表只含 `block_id/state/attempts/retryable/failure_code`，**不返回正文、向量、
  异常原文**；不改动 `/jobs` 列表的现有字段。

## 三、验证证据

- 后端完整回归 **1165 passed**（150.45 秒，4 个既有警告），全部使用真实 PostgreSQL
  `campusflow_test`；本机 Ruff 检查与格式检查通过。
- 新增测试文件：`test_index_orchestration.py`（16）、`test_index_pipeline_jobs.py`（11）、
  `test_index_recovery.py`（11）、`test_index_progress_api.py`（7）、
  `test_index_schema.py`（5）、`test_index_e2e.py`（2），以及辅助 `index_helpers.py`。
  覆盖：完整流程、重复消息、永久/临时失败、3 次耗尽、租约过期接管、运行中删除资料、
  版本被取代、来源文本变化后不重做已完成块、归档来源、覆盖未知 → partial、空版本、
  解析成功恰好建一个 embed 作业、投递失败留待补偿、阶段感知回收、心跳新鲜不回收、
  自动重投上限、后台循环异常隔离与取消、跨空间 404、删除资料级联清理。
- 迁移往返（临时库 `campusflow_q004_roundtrip_test`）：
  `upgrade head → downgrade q004_index_manifests → upgrade head` 全部成功；临时库已删除。
  开发库 `campusflow` 全程未触碰。
- **Docker 真实队列冒烟（2026-10-17，临时库 `campusflow_q004_demo_test` + Redis db 15 + `--demo`）**：
  1. 种子 `.txt` 资料 → 投递 `parse-1` → Docker Worker `--burst --demo` 解析成功，
     资料状态 `done`，自动生成一个 `pending` 且未投递的 embed 作业（id=2）；
  2. 在宿主机执行 `run_recovery_sweep`，真实 Redis 中出现 `index-2`（outbox 补投生效）；
  3. 再次运行 Docker Worker，`index-2` 成功，向量落库（1024 维），作业 `done`，
     汇总 `expected=1 / indexed=1 / fully_searchable=true`；
  4. 故意重新投递同一索引作业的消息，Worker 正常结束但**没有重复调用模型或重复写入**
     （块数仍为 1，attempts 仍为 1）；
  5. HNSW 索引存在且 `indisvalid=true`。
  冒烟结束后容器、临时库、Redis db 15 与临时脚本/存储目录均已清理。

## 四、已知限制与后续

- **粘贴文本（`import_pasted_text`）暂不创建索引作业**：它现在同步解析、不产生后台
  作业（既有测试 `test_text_has_no_background_jobs` 明确锁定）。文件上传与网页导入
  已走完整后台链路；粘贴文本的索引作为后续小任务，不在本轮偷偷改变产品行为。
- 不批量化向量调用（用户已选一块一次）；不改真实 `ZhipuEmbedder` 的调用细节。
- 外部调用成功但保存前崩溃仍可能再次调用，不承诺模型恰好调用一次（租约保证不会
  写坏数据，不保证零重复计费）。
- 本轮所有验证使用假向量器，未产生模型费用；真实模型效果需单独评测。
- HNSW 当前为普通建索引；表规模变大后应改为并发建索引并评估 `ef_search`。
- Windows 原生无法运行 Worker，仍使用 Docker（现状不变）。

## 五、验收命令（在 `backend/` 下）

```bash
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

运行前需 `uv run --locked alembic upgrade head`。Worker 运行方式见
[MATERIAL_PROCESSING.md](../MATERIAL_PROCESSING.md)。
