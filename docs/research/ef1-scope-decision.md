# EF1 结论：桌面分发与技术栈演进的后续处理

- Issue：[#380（EF1）](https://github.com/LUNARKN1GHT/CampusFlow/issues/380) · 子任务：[#381（F005）](https://github.com/LUNARKN1GHT/CampusFlow/issues/381)、[#382（F006）](https://github.com/LUNARKN1GHT/CampusFlow/issues/382)
- 范围：后续探索结论；不改变现有三阶段划分与版本承诺
- 日期：2026-10-21

## 结论摘要

两项调研均不立项，也不纳入现有三阶段交付：桌面分发保持“浏览器 + 本机服务”，近期如需双击启动则走 Docker 一键本机包（实现由 #400 单独暂缓跟踪）；后端保持 Python 3.12 + FastAPI，不迁移、不整体重写，先按 F006 的测量基线收集数据。

## 桌面分发（F005）

**决策**：维持浏览器 + 本机服务形态；近期若需要“双击启动”，采用 F005 路线 A（Docker 一键本机包）。实现由 [#400](https://github.com/LUNARKN1GHT/CampusFlow/issues/400) 单独跟踪，2026-10-19 作者已决定暂缓实施。Electron／Tauri 原生外壳暂不立项。

**理由**：

- 难点不在桌面外壳，而在 PostgreSQL、Redis、RQ Worker 的携带、升级、备份与跨平台签名；路线 A 完全复用现有技术栈。
- 路线 A 预估 3–5 个工作日；Electron／Tauri 连同本地服务分别约 3–6 周和 4–8 周，且维护成本更高。
- 当前没有 Windows/macOS 双实机验证条件，也没有达到必须原生安装的用户规模。

**再评估触发条件**：

- 课程演示或真实用户明确要求“双击安装的普通 App”；
- 具备 Windows 与 macOS 实机、Docker Desktop 和双平台验证能力；
- 若走原生外壳，先验证 PostgreSQL／Redis／Worker 的随包携带或裁剪方案。

## 后端技术栈（F006）

**决策**：保持 Python 3.12 + FastAPI，不迁移、不整体重写。先按 F006 的测量基线收集真实使用数据；只有某个指标连续触发阈值且低风险优化无效时，才对该上下文走分批迁移。

**理由**：

- 当前面向单用户工作空间，没有性能或规模数据能证明 Python 后端已成为瓶颈。
- 分批迁移一个上下文约 2–4 个月，整体重写 6–12 个月，期间还要重建数据库迁移历史、核对留痕与引用可追溯性。
- 领域层／应用层边界让局部替换可行，但这是触发后的选项，不是当前计划。

**再评估触发条件**：F006「触发重构的可量化条件」表中的阈值与测量方法；没有连续实测数据不得启动迁移。

## 开放问题去向

| 开放问题 | 去向 |
| --- | --- |
| Docker 一键本机包实现 | [#400](https://github.com/LUNARKN1GHT/CampusFlow/issues/400)（open，暂缓） |
| 原生桌面外壳与签名／公证／自动更新 | 不立项；再评估触发条件见 F005 |
| 后端性能基线与迁移阈值 | 不单独立项；按 F006 的 6 步测量基线收集，触发后另开限定范围的 Issue |
| 完全离线使用 AI | 明确不做；桌面打包不等于 AI 离线可用 |

## 验收证据

- F005 交付物：[f005-desktop-distribution.md](./f005-desktop-distribution.md)，PR [#410](https://github.com/LUNARKN1GHT/CampusFlow/pull/410)。
- F006 交付物：[f006-backend-stack-evolution.md](./f006-backend-stack-evolution.md)，PR [#411](https://github.com/LUNARKN1GHT/CampusFlow/pull/411)。
- 子任务验收：两份文档分别覆盖各自 Issue 的四项验收条件（方案对比／依赖处理／签名与更新成本／立项建议；量化阈值／候选路径／既有约束／立项建议）。
- 工作量：EF1 父 Issue 不单独统计；两个叶子任务各按 `size:M` 记录。

## 明确不做

- 不新增 Dockerfile、compose、启动脚本或签名配置；#400 仍为暂缓状态。
- 不迁移技术栈、不改写 Alembic 迁移历史、不改变 `/api/v1` 契约。
- 不承诺完全离线使用 AI 能力，不改变 README 的分阶段交付承诺。
