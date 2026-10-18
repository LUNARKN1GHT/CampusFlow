# Q005：检索前限定资料范围

日期：2026-10-18。作者：金嘉睿。任务 #132，父任务 #13。

## 用户已确认的规则

指定学期、课程或教学班时，只使用明确匹配的资料。该字段为空的资料不作为通用资料混入结果；需要用户补齐分类后才能在该范围内找到。未提供某个筛选维度表示不限制该维度，工作空间始终受限。

## 已实现

- `application/retrieval.py::collect_retrieval_candidates` 校验当前工作空间以及用户所选学期、课程的归属。外部空间编号与不存在编号都按不存在处理，不泄露对象信息；课程与所选学期或已知教学班矛盾时拒绝读取。
- `infrastructure/retrieval.py::SqlRetrievalCandidates` 在 SQL 中先限制空间、学期、课程、教学班、非归档资料及版本，再加载原文和向量。不是全库排序后再删掉不符合范围的结果，也不让模型猜适用对象。
- 同时检查资料、索引作业、清单、向量块的空间关系和版本关系。只使用当前 `embed` 作业关联清单、指定模型配置及清单内成功块，不混用相同维度但不同代次的向量。
- 复用 Q004 完整来源清单校验；原文、位置、解析覆盖或片段集合改变后，旧结果不可用。逐块再校验来源、文本范围和向量；发现无效数据时该版本整体排除，并返回 `index_unavailable`，不默默声称资料齐全。数据库及未预期程序故障仍抛出。
- 成功块保留版本、原片段、位置和字符范围；同时返回整份资料的索引进度及解析覆盖。部分成功仍可提供有效原文，未完成块和未知覆盖保留给后续回答策略使用。
- 资料归档、恢复、修改范围、删除的现有状态每次查询重新读取。学期归档沿用历史可查询语义；明确查询该学期时不会自动删掉历史资料。

## 接入方式与边界

应用入口接收已认证会话确定的 `workspace_id`，不能使用模型输出作为授权依据。`RetrievalScope` 仅表达用户选择，不根据问题文字自动扩大学期或课程。

```python
with Session(engine) as session:
    result = collect_retrieval_candidates(
        build_repositories(session),
        SqlRetrievalCandidates(session),
        authenticated_workspace_id,
        RetrievalScope(semester_id=semester_id, course_id=course_id, class_name=class_name),
        configured_embedding_profile_id,
    )
# 关闭读取事务后，Q006 才对 result.candidates 排序；以后 Q011 再组装模型上下文。
```

- 本任务是可调用、经集成测试的候选读取入口，不新增 HTTP 接口或问答页面。Q006 负责检索排序比较，Q007 负责公开引用编号，Q011/Q012 负责生成答案及覆盖提示。
- `versions` 只描述进入当前配置索引清单的版本，不能据空结果认定“没有作业”，也不能把它当成资料库总清单；尚无索引作业、被重置或使用其他配置的资料不会返回候选。
- 版本过滤沿用 Q004 的同一资料记录内当前存储版本限制。独立导入的不同通知不按导入时间互相替代；本功能不判定哪份业务通知生效，不代替 N010–N014 的变更核对。
- 当前实现读取限定范围的全部有效候选，并按资料、片段、偏移固定排序。没有提前截断或假定 HNSW 已参与查询；关键词/向量排名策略与性能评测留给 Q006。
- 为复用 Q004 的一致性检查，读取期间持有资料及来源行锁，调用者必须及时结束事务，不能在该事务内等待外部模型。大资料量下的查询优化和回答期间发生删除等竞态仍须在 Q006/Q008 后续验证。
- 无数据库迁移、无新增依赖、无真实模型请求。粘贴文本尚不自动建立索引作业的 Q004 限制仍然存在。

## 验证记录

- 专项 `tests/test_retrieval_scope.py`：35 项通过，使用独立 PostgreSQL 库 `campusflow_q005_test` 和合成原文、假向量器。
- 覆盖：各范围独立筛选、空分类、同名不同班、跨空间编号、范围矛盾、过滤先于来源读取、归档恢复及范围修改、学期归档、删除、新存储版本、重置作业、来源/位置/覆盖变更、错误向量、部分成功、配置隔离及当前清单选择。
- 前端 `npm run build`（含类型检查）通过。
- 全量后端回归（`pytest -q`，专用库 `campusflow_q005_test`）：1230 项通过，耗时 133.69 秒。
- `ruff check .`：All checks passed。
- `ruff format --check .`：235 个文件已格式化。

专项复验（在 `backend/` 下，测试库名必须以 `_test` 结尾）：

```powershell
$env:CAMPUSFLOW_TEST_DATABASE_URL='postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_q005_test'
uv run --locked pytest tests/test_retrieval_scope.py -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```
