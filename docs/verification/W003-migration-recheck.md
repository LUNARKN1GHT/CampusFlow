# W003 复验：基础迁移的升级与回退

- Issue：#47（W003）· 父 Issue #5（E10）
- 验证日期：2026-09-30
- 执行人：24151735

## 环境

| 项 | 值 |
| --- | --- |
| 操作系统 | Windows 11 Home 10.0.26200 |
| Docker | Docker Desktop 29.8.0（WSL2 后端） |
| PostgreSQL | pgvector/pgvector:pg17 容器（127.0.0.1:5432） |
| Python | 3.12.10，依赖由 uv.lock 锁定 |
| Alembic | `uv run --locked alembic`（版本随锁文件） |
| 目标库 | `campusflow_test`（独立测试库） |

开发库 `campusflow` 全程未参与本次验证，验证前后数据完好（1 工作空间 / 1 学期 / 1 课程 / 1 任务）。

## 执行命令与结果

```bash
cd backend
export CAMPUSFLOW_DATABASE_URL="postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_test"

# 1. 回退到起点（空库）
uv run --locked alembic downgrade base
#   结果：Running downgrade c95d3606e7ee -> ，六张业务表全部删除

# 2. 空库升级到最新
uv run --locked alembic upgrade head
#   结果：Running upgrade  -> c95d3606e7ee，六张表创建成功

# 3. 验证表清单
docker exec campusflow-db psql -U campusflow -d campusflow_test -c "\dt"
#   结果：alembic_version、availability_slots、courses、fixed_events、
#        semesters、tasks、workspaces 共 7 张（含版本表）

# 4. 再次回退
uv run --locked alembic downgrade base
#   结果：六张业务表删除，仅剩 alembic_version

# 5. 再次升级
uv run --locked alembic upgrade head
#   结果：升级成功，六张表恢复

# 6. 外键一致性检查
docker exec campusflow-db psql -U campusflow -d campusflow_test -c \
  "SELECT conrelid::regclass AS 表, conname AS 外键名 FROM pg_constraint WHERE contype='f' ORDER BY 1,2;"
```

外键清单（8 个，与 `backend/src/campusflow/infrastructure/db/models.py` 一致）：

| 表 | 外键 |
| --- | --- |
| availability_slots | → workspaces |
| semesters | → workspaces |
| courses | → semesters、workspaces |
| fixed_events | → courses、workspaces |
| tasks | → courses、workspaces |

## 结论

- 空库升级、回退、再次升级两轮循环全部成功 ✅
- 六张表与 8 个外键和 ORM 模型一致 ✅
- 开发数据未被测试清空 ✅
- 验证过程与结果已保存于本文档，命令可复现 ✅
