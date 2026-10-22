# P005 桌面 DB 轮询 Worker 验收

对应 Issue：#471。实现分支：`issue/#471`。合并验证分支：`integration/desktop-p001-p005`。

## 交付

- `infrastructure/queue/db_queue.py`：`DBJobQueue` 只返回稳定本地标识，不连接 Redis，也不导入 `redis`／`rq`。
- `infrastructure/queue/factory.py`：`server` 装配 `RQJobQueue`（延迟导入），`desktop` 装配 `DBJobQueue`；未知模式拒绝。
- `application/ports/repositories.py` 与仓储实现新增 `list_claimable_ids(limit)`：仅 parse/embed 且状态为 pending，按编号升序；实际领取仍复用既有原子 `claim`。
- `workers/assembly.py`：解析器与模型装配从 RQ 入口抽出，供两种 Worker 共用。
- `workers/desktop.py`：数据库轮询循环，支持 `--burst`、`--poll-interval`、`--batch-size`、SIGINT/SIGTERM 停止、启动恢复扫描；单作业失败不终止循环；不依赖 fork、Linux 专用队列或 `os.name == "nt"` 拒绝。
- `workers/jobs.py`：作业结束时释放引擎连接，避免长驻轮询累积连接。

## 验证证据

- 后端全量（合并后，独立测试库）：`1583 passed, 6 skipped`。
- `backend/tests/test_desktop_worker.py`：10 passed，覆盖无 Redis 上传→解析→索引闭环（图片、PDF、DOCX）、重复投递／双 Worker 只执行一次、解析与索引重启恢复、可领取查询语义、桌面模块导入隔离（`redis`/`rq` 不在 `sys.modules`）、服务端仍选择 RQ、未知模式被拒绝。
- `uv run ruff check .`：`All checks passed!`。
- 手工 `--burst`（隔离测试库，预置 1 个 pending 索引作业）：

```
desktop_recovery: 投递 1 条，回收 0 个，自动重投 0 个
desktop_worker_stopped: processed=1
EXIT=0
```

随后查询：作业 `embed/done/attempts=1`，两个块均 `done/attempts=1`。

## 未验证

- Windows 实机运行；代码未使用平台限定 API，但未在 Windows 上执行。
- 真实 Redis 服务端 RQ 回归；服务端选择由 fakeredis 套件覆盖。
- 真实视觉／向量模型调用；手工与测试均使用 `--demo` 假识别与假向量。
- SIGINT/SIGTERM 的自动化测试；`--burst` 与停止标记逻辑已实现，手工仅验证 burst。
