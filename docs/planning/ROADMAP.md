# CampusFlow 开发路线与任务管理

本规划以 README 的三个交付阶段为范围，以 `b63018e` 的实际代码为基线。目标仓库为 [LUNARKN1GHT/CampusFlow](https://github.com/LUNARKN1GHT/CampusFlow)，学校 GitLab 不重复建立任务。

- [Milestone 总览](https://github.com/LUNARKN1GHT/CampusFlow/milestones)
- [Issue 列表](https://github.com/LUNARKN1GHT/CampusFlow/issues)
- [Labels](https://github.com/LUNARKN1GHT/CampusFlow/labels)
- [逐项任务索引](ISSUES.md) · [初始结构化清单](backlog.json) · [GitHub 编号映射](github-map.json) · [发布校验记录](audit.json)

## 里程碑与发布条件

不预设开发日期。各阶段可以在接口和依赖明确后并行推进；后续里程碑的正式验收以先前里程碑通过为前提。单个任务正文列出实际前置依赖，不要求所有成员等待前一阶段全部结束才开始样本、设计等独立工作。

| Milestone | 产品阶段 | 验收后的能力 | 主要完成条件 | 目标版本 |
| --- | --- | --- | --- | --- |
| [M0 工程基线与验收准备](https://github.com/LUNARKN1GHT/CampusFlow/milestone/1) | MVP 准备 | 团队能按一致流程开发和判断成果 | 已有实现补录；开发和 CI 检查可复现；授权或合成样本、八项验收用例和关键原型齐全 | 不打 tag |
| [M1 手动学习事务管理](https://github.com/LUNARKN1GHT/CampusFlow/milestone/2) | MVP | 通过界面维护学期、课程、任务、固定日程与可用时间 | 真实 PostgreSQL 复验；身份和空间授权；前后端闭环；日期、时区、状态正确 | `v0.1.0` |
| [M2 资料导入与来源定位](https://github.com/LUNARKN1GHT/CampusFlow/milestone/3) | MVP | PDF、图片和粘贴文本可私有保存、解析、查看原文 | 定位准确；部分失败可见；作业可重试且幂等；删除影响和派生失效正确 | `v0.2.0` |
| [M3 事项提取与人工核对](https://github.com/LUNARKN1GHT/CampusFlow/milestone/4) | MVP | 从资料获得有证据的候选，并确认成正式事项 | 缺失信息不补造；集中核对；确认幂等；核对与执行状态独立 | `v0.3.0` |
| [M4 带引用的问答](https://github.com/LUNARKN1GHT/CampusFlow/milestone/5) | MVP | 查询课程要求并检查支持答案的原文 | DDL 与引用一致；无资料和冲突明确说明；范围隔离；中文样本评估 | `v0.4.0` |
| [M5 两周计划与 MVP 验收](https://github.com/LUNARKN1GHT/CampusFlow/milestone/6) | MVP 完成 | 生成、接受、执行未来两周计划并检查风险 | 排期约束、缺口展示、草案版本确认有效；README 八项 MVP 验收通过 | `v0.5.0` |
| [M6 扩展导入与通知变更](https://github.com/LUNARKN1GHT/CampusFlow/milestone/7) | 第二阶段 | 导入 Word、公开网页；比较和接受版本差异 | 保留来源；不按导入时间覆盖；显示影响；合并和变更可追溯 | `v0.6.0` |
| [M7 日常执行与局部调整](https://github.com/LUNARKN1GHT/CampusFlow/milestone/8) | 第二阶段完成 | 依赖任务、局部重排、应用内提醒与导出 | 保留锁定安排；提醒去重；导出时区和隐私正确；第二阶段验收通过 | `v0.7.0` |
| [M8 课程关系与修读路径](https://github.com/LUNARKN1GHT/CampusFlow/milestone/9) | 第三阶段 | 基于适用培养方案探索修读顺序 | 三类关系分开；环、缺项和矛盾阻塞可见；不冒充毕业资格审核 | `v0.8.0` |
| [M9 外部日历与完整流程验收](https://github.com/LUNARKN1GHT/CampusFlow/milestone/10) | 第三阶段完成 | 经确认写入一个选定日历服务并跟踪结果 | 确认范围与执行一致；逐项结果、撤销、幂等；三阶段回归通过 | `v0.9.0` |

M1 与历史 PR 的“M1 持久化切片”接续，但新增了前端、访问控制和复验条件。PR #1 合并不代表整个 M1 已验收。所有里程碑初始保持开放。

Word、公开网页、依赖任务、局部重排和提醒保持第二阶段；课程路径与外部日历保持第三阶段。后续探索不设置交付 Milestone。既有 [#2：优化查询方式](https://github.com/LUNARKN1GHT/CampusFlow/issues/2) 的 QQbot 建议保留作者原文，归入探索组，先做授权与可行性决策。

## 如何使用 Issue 与 Sub-issue

本次建立 25 个父 Issue 和 221 个叶子任务，另将已有 #2 纳入探索父 Issue。原生父子关系使功能组可查看子任务进度；跨组前置关系通过正文中的 Issue 链接表达。父 Issue 是聚合入口，不重复计入开发工作量。

每个叶子任务包括目标与交付物、具体验收条件、项目依据、前置任务、规模与提交证据要求。拆分尺度是能由一人独立交付和验收的一项行为，而不是一个空类、空接口或机械地把每个文件变为任务。实现和必要验证放在同一任务；跨模块集成、难例评估和发布验收另设测试任务。

1. 从当前里程碑中选取前置任务已完成的叶子 Issue，填写 Assignee，并在认领说明中写明预计交付物。
2. 规模参考：`XS` 半天以内，`S` 半天至一天，`M` 一至两天。根据个人熟练程度校准；预计超过两天，继续建立更小的原生 Sub-issue，避免一项任务长期只有“进行中”。
3. 每人建议同时推进一个叶子任务。开始后标记 `status:in-progress`；阻塞时写明具体前置条件并改为 `status:blocked`。
4. PR 关联叶子 Issue，附与验收条件对应的实际结果；进入评审后标记 `status:in-review`。本地未运行的测试不能写为通过。
5. 其他成员确认验收结果后关闭任务，并清除活动状态标签。父 Issue 等全部子任务验收和集成演示完成后再关闭，不被某一个子任务 PR 提前关闭。
6. Milestone 除子任务完成外，还需通过其描述中的业务门槛；组长确认后关闭并准备版本发布。

新增需求先检查现有任务。调整范围时更新 GitHub 描述、依赖及相关规划文档；不要重新运行初始清单去覆盖成员的认领、讨论或进度。

## 标签规则

| 标签维度 | 用途 | 使用约定 |
| --- | --- | --- |
| `type:*` | epic、feature、bug、test、docs、chore、research、design、refactor | 每项一个，表明交付类型 |
| `area:*` | 课程、资料、解析、核对、问答、计划等业务模块 | 每项一个主模块，必要时可增加相关模块 |
| `layer:*` | backend、frontend、fullstack、infra、docs | 叶子任务一个，用于选择技术范围 |
| `priority:P0` | 阻塞所属里程碑验收或关键前置 | 不代表第三阶段任务要立即开工 |
| `priority:P1` | 所属里程碑的正常交付内容 | 按前置依赖推进 |
| `priority:P2` | 可后置优化或未立项探索 | 不自动升级为 MVP 范围 |
| `size:XS/S/M` | 认领时的规模参考 | 只用于叶子任务，超出两天继续拆分 |
| `status:in-progress/blocked/in-review` | 活动状态 | 同时只保留一个；待认领使用开放且无活动状态标签 |
| `status:backfilled` | 已合并实现的历史补录 | 不表示测试或整个 Milestone 已通过 |
| `status:needs-decision` | 需要方案或范围决策 | 适用于后续探索，不直接开始大规模实现 |
| `good first issue` | 边界清晰的入门任务 | 仍需检查前置依赖和认领 |

共维护 44 个规划标签，保留 GitHub 原有标签。新任务以带前缀的分类为准，不把 Git 版本 tag 当作 Issue 标签，也不批量删除历史标签。

## 已提前完成的工作

以下 5 个叶子任务按“代码实现已合并”的窄范围补录关闭。验收与缺陷补齐保持开放，未将这些记录包装成完整产品能力。

| 规划编号 | 已有证据 | 仍需完成 |
| --- | --- | --- |
| B001 | `04c4da8`：Vue、FastAPI、健康接口和工程配置 | CI、原型、样本和整体运行复验 |
| W001 | `42bebb7`：PostgreSQL、SQLAlchemy 与六张表初始迁移 | 空库升级回退复验、就绪检查与空间授权 |
| W002 | PR #1：学期与课程 API | 业务页面、归档语义、必填空值校验、访问控制 |
| T001 | PR #1：手动任务、进度、日期精度 | 状态历史、个人目标、逾期与剩余耗时、接口复验 |
| T002 | PR #1：固定日程和每周可用时间 | 重复展开、时区设置、容量规则与业务界面 |

GitHub 确认 PR #1 的创建者和合并者为 **24151735**，功能提交作者字段为 **IVANLEE**。这些是不同类型的活动记录，不据此推断某个人编写了全部代码，也不自动给未来任务分配负责人。

PR 正文声称 22 个测试通过；本次规划会话未在当前机器复验这些业务测试，现有环境缺少 Docker 命令及部分离线依赖。W003、T003 专门跟踪独立复验；T004、T005 跟踪纯领域测试隔离与测试数据库保护。

## 团队认领建议

当前只提供方向建议，未擅自填写成员的 GitHub Assignee，具体分工仍由小组认领决定。

| 成员 | 建议优先认领的工作 | 配合方式 |
| --- | --- | --- |
| 李亦凡 | 跨层接口、权限、事务幂等、排期算法、集成评审 | 先明确关键接口和验收，复核高风险实现 |
| 金嘉睿 | PDF/文本解析、后台作业、公开网页导入及对应样本测试 | 从一个适配器或明确输入格式开始 |
| 吴思彤 | 手动管理用例、前端表单、任务进度、核对页面或接口测试 | 一次交付一个有 API 契约的功能 |
| 顾昕越 | 样本授权登记、脱敏合成样本、验收表、原型走查、操作文档 | 优先 `good first issue`，验收结论由实现者共同复核 |

M0 优先开始 B003、B004、B009–B013；已合并数据库基础允许同时认领 W003、W004、T003。开始编码前在 Issue 记录负责人，避免再次出现大块功能先完成、任务管理再补录的情况。

## Git 版本 tag 规则

- 正式版本采用 `vMAJOR.MINOR.PATCH`；当前预稳定阶段使用 `v0.1.0` 至 `v0.9.0` 对应 M1 至 M9，通过各自门槛后发布。兼容修复递增 PATCH，例如 `v0.5.1`。
- 发布候选采用 `v0.MINOR.0-rc.N`，例如 `v0.5.0-rc.1`。候选不代替正式验收；没有通过验收的实现不打正式 tag。
- M0 仅是基线准备；不给历史 PR #1 追溯创建“已验收版本”。
- 发布前确认目标提交已合并到 `main`、相关检查与业务验收通过、发布说明列出能力、限制、迁移及回滚步骤。
- 使用附注 tag 指向确切提交，并在发布说明链接里程碑及验收证据。已发布 tag 不移动或覆盖；修复生成新版本。
- `v1.0.0` 需另行确定稳定性、部署与兼容承诺，M9 完成不自动等于生产可用。
- 本次只制定规则，不创建任何 Git tag。

## 规划文件与校验

`backlog.json` 保存本次初始范围；`github-baseline.json` 保留必要的发布前状态，用于核对已有内容没有被覆盖；`github-map.json` 保存规划编号、GitHub 编号与初次发布完成标记；`ISSUES.md` 提供可点击索引；`audit.json` 记录发布时对远程真实状态的核验结果。后续任务状态以 GitHub 为准。

```bash
python3 scripts/project_planning.py validate
python3 scripts/project_planning.py render
```

上述命令只验证或渲染本地快照，不修改 GitHub。初次发布工具使用明确仓库名、稳定标记和串行请求，可在中断后从远程已有条目恢复；一次性发布审核通过后会拒绝重复发布，以免覆盖成员后续操作。发布审核包括全部标题、正文、标签、里程碑、初始状态、原生父子关系、既有建议保留及版本 tag 未变化。它验证管理结构，不等同于业务功能测试。

原生子任务实现参考 [GitHub Sub-issues API](https://docs.github.com/en/rest/issues/sub-issues)，批量请求遵守 [GitHub API 速率限制](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)。
