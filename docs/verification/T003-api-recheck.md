# T003 接口集成与日期精度复验

日期：2026-10-12。叶子 Issue：#58；父 Issue：#6（E11）；目标分支：`issue/#58` → `issue/#6`。本记录只覆盖 T003，不代表 E11 或 M1 验收完成。

## 验收对照

- **记录既有测试的实际结果**：立项时口径为 22 个既有测试；当前 develop 全量套件已扩展到 66 个（含本项新增的持久化重读用例），全部在真实 PostgreSQL 上执行通过，结果如下。
- **日期 DDL 不被补成午夜或 23:59**：由两条用例佐证——`test_date_only_ddl_keeps_date_precision` 断言 API 响应不含 `00:00:00`／`23:59:00`；本项新增 `test_date_only_ddl_persisted_without_fabricated_time` 用全新数据库连接直读 `tasks` 表，断言 `due_date = 2026-10-15` 且 `due_time IS NULL`，即持久化层同样没有补造时刻。

## 执行环境与命令

环境：macOS，Python 3.12，uv 锁定依赖，本机 PostgreSQL（`pg_isready` 接受连接）。按 E10 验收的既有做法新建专用库 `campusflow_t003_test`，避免污染含其他集成分支迁移的默认 `campusflow_test`；开发库未参与。

```bash
cd backend
CAMPUSFLOW_TEST_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_t003_test uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

结果：**66 passed**；Ruff lint 与 format 检查通过。仅有 Starlette TestClient httpx 与 Alembic path_separator 两组既有弃用警告，与 E10 记录一致，不影响结果。

## 限制

- 默认 `campusflow_test` 的 `alembic_version` 停留在其他集成分支（E22 链）的修订上，直接在它上面跑 develop 迁移会报 `Can't locate revision`，因此本项使用独立新库；该库未被修改。
- 本项只复验 PR #1 以来的接口集成与日期精度，不为 T004–T012 的任何口径作证。
