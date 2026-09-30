# CampusFlow 开发任务索引

本次规划：10 个 Milestone、25 个父 Issue、221 个叶子任务。另保留既有 QQbot 建议 #2。

叶子任务以半天至两天为参考规模；超过两天继续拆分。规模是认领参考，不是交付日期承诺。
GitHub 是发布后的任务状态与认领信息来源；本文件和 backlog.json 是初始范围快照，不用于覆盖后续状态。

规划规则见 [ROADMAP.md](ROADMAP.md)，结构化清单见 [backlog.json](backlog.json)。

## M0 · 工程基线与验收准备

### [[E00] 工程基线与协作流程](https://github.com/LUNARKN1GHT/CampusFlow/issues/3)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [B001 · 补录：Vue 与 FastAPI 健康检查骨架已合并](https://github.com/LUNARKN1GHT/CampusFlow/issues/28) | 已合并补录 | XS | 无 |
| [B002 · 同步项目当前实现状态与规划入口](https://github.com/LUNARKN1GHT/CampusFlow/issues/29) | 待认领 | S | #28（B001） |
| [B003 · 建立小任务与缺陷 Issue 模板](https://github.com/LUNARKN1GHT/CampusFlow/issues/30) | 待认领 | XS | 无 |
| [B004 · 建立 PR 模板与关闭 Issue 约定](https://github.com/LUNARKN1GHT/CampusFlow/issues/31) | 待认领 | XS | 无 |
| [B005 · 配置后端 CI：PostgreSQL 集成测试与 Ruff](https://github.com/LUNARKN1GHT/CampusFlow/issues/33) | 待认领 | M | #32（W001） |
| [B006 · 配置前端 CI：锁文件安装与类型构建](https://github.com/LUNARKN1GHT/CampusFlow/issues/34) | 待认领 | S | #28（B001） |
| [B007 · 制定并落实默认分支合并检查](https://github.com/LUNARKN1GHT/CampusFlow/issues/35) | 待认领 | S | #31（B004）, #33（B005）, #34（B006） |
| [B008 · 确定许可证并同步正式声明](https://github.com/LUNARKN1GHT/CampusFlow/issues/36) | 待认领 | XS | 无 |

### [[E01] 样本、验收集与交互原型](https://github.com/LUNARKN1GHT/CampusFlow/issues/4)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [B009 · 建立样本授权与脱敏登记表](https://github.com/LUNARKN1GHT/CampusFlow/issues/37) | 待认领 | S | 无 |
| [B010 · 制作文本 PDF 与跨页表格样本](https://github.com/LUNARKN1GHT/CampusFlow/issues/38) | 待认领 | S | #37（B009） |
| [B011 · 制作模糊扫描和不完整截图样本](https://github.com/LUNARKN1GHT/CampusFlow/issues/39) | 待认领 | S | #37（B009） |
| [B012 · 制作日期、同名课程和来源冲突样本](https://github.com/LUNARKN1GHT/CampusFlow/issues/40) | 待认领 | S | #37（B009） |
| [B013 · 建立 MVP 八项验收用例表](https://github.com/LUNARKN1GHT/CampusFlow/issues/41) | 待认领 | S | #38（B010）, #39（B011）, #40（B012） |
| [B014 · 绘制资料导入与原文查看原型](https://github.com/LUNARKN1GHT/CampusFlow/issues/42) | 待认领 | S | #41（B013） |
| [B015 · 绘制集中核对与问答原型](https://github.com/LUNARKN1GHT/CampusFlow/issues/43) | 待认领 | S | #41（B013） |
| [B016 · 绘制任务与两周计划原型](https://github.com/LUNARKN1GHT/CampusFlow/issues/44) | 待认领 | S | #41（B013） |
| [B017 · 定义样本评估指标与记录格式](https://github.com/LUNARKN1GHT/CampusFlow/issues/45) | 待认领 | S | #41（B013） |

## M1 · 手动学习事务管理

### [[E10] 持久化、工作空间与课程](https://github.com/LUNARKN1GHT/CampusFlow/issues/5)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [W001 · 补录：PostgreSQL 与六张基础表迁移已合并](https://github.com/LUNARKN1GHT/CampusFlow/issues/32) | 已合并补录 | XS | 无 |
| [W002 · 补录：学期与课程 API 已合并](https://github.com/LUNARKN1GHT/CampusFlow/issues/46) | 已合并补录 | XS | #32（W001） |
| [W003 · 复验基础迁移的升级与回退](https://github.com/LUNARKN1GHT/CampusFlow/issues/47) | 待认领 | S | #32（W001） |
| [W004 · 实现单用户身份校验与会话退出](https://github.com/LUNARKN1GHT/CampusFlow/issues/48) | 待认领 | M | #32（W001） |
| [W005 · 将当前工作空间注入应用用例](https://github.com/LUNARKN1GHT/CampusFlow/issues/49) | 待认领 | S | #48（W004） |
| [W006 · 为对象读取、修改及关联增加空间授权](https://github.com/LUNARKN1GHT/CampusFlow/issues/50) | 待认领 | M | #49（W005） |
| [W007 · 保存个人时区与每周学习容量设置](https://github.com/LUNARKN1GHT/CampusFlow/issues/51) | 待认领 | S | #49（W005） |
| [W008 · 让日期时间转换使用工作空间时区](https://github.com/LUNARKN1GHT/CampusFlow/issues/52) | 待认领 | S | #51（W007） |
| [W009 · 明确并实现学期归档后的查询规则](https://github.com/LUNARKN1GHT/CampusFlow/issues/53) | 待认领 | S | #46（W002） |
| [W010 · 修复 PATCH 将必填字段置空的问题](https://github.com/LUNARKN1GHT/CampusFlow/issues/56) | 待认领 | S | #46（W002）, #54（T001）, #55（T002） |
| [W011 · 增加数据库就绪检查](https://github.com/LUNARKN1GHT/CampusFlow/issues/57) | 待认领 | S | #32（W001） |

### [[E11] 手动任务与时间约束](https://github.com/LUNARKN1GHT/CampusFlow/issues/6)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [T001 · 补录：手动任务 API 与进度枚举已合并](https://github.com/LUNARKN1GHT/CampusFlow/issues/54) | 已合并补录 | XS | #32（W001） |
| [T002 · 补录：固定日程与可用时间 API 已合并](https://github.com/LUNARKN1GHT/CampusFlow/issues/55) | 已合并补录 | XS | #32（W001） |
| [T003 · 复验 PR #1 的接口集成与日期精度](https://github.com/LUNARKN1GHT/CampusFlow/issues/58) | 待认领 | S | #47（W003）, #54（T001）, #55（T002） |
| [T004 · 分离无需数据库的领域单元测试](https://github.com/LUNARKN1GHT/CampusFlow/issues/59) | 待认领 | S | #58（T003） |
| [T005 · 保护测试数据库目标并隔离测试配置](https://github.com/LUNARKN1GHT/CampusFlow/issues/60) | 待认领 | S | #47（W003） |
| [T006 · 增加任务进度变更历史与调整原因](https://github.com/LUNARKN1GHT/CampusFlow/issues/61) | 待认领 | S | #54（T001）, #50（W006） |
| [T007 · 增加个人目标日期与正式 DDL 的区分](https://github.com/LUNARKN1GHT/CampusFlow/issues/62) | 待认领 | S | #54（T001） |
| [T008 · 计算逾期标记并保留执行状态](https://github.com/LUNARKN1GHT/CampusFlow/issues/63) | 待认领 | S | #54（T001）, #52（W008） |
| [T009 · 增加任务剩余耗时维护](https://github.com/LUNARKN1GHT/CampusFlow/issues/64) | 待认领 | S | #61（T006） |
| [T010 · 为任务列表增加学期与截止区间筛选](https://github.com/LUNARKN1GHT/CampusFlow/issues/65) | 待认领 | S | #53（W009）, #54（T001） |
| [T011 · 实现每周日程在指定日期范围内展开](https://github.com/LUNARKN1GHT/CampusFlow/issues/66) | 待认领 | S | #55（T002）, #52（W008） |
| [T012 · 定义可用时间重叠与跨日录入规则](https://github.com/LUNARKN1GHT/CampusFlow/issues/67) | 待认领 | S | #55（T002）, #52（W008） |

### [[E12] 基础业务前端](https://github.com/LUNARKN1GHT/CampusFlow/issues/7)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [U001 · 建立业务页面导航与路由](https://github.com/LUNARKN1GHT/CampusFlow/issues/68) | 待认领 | S | #28（B001）, #42（B014）, #43（B015）, #44（B016） |
| [U002 · 建立统一业务 API 客户端与错误展示](https://github.com/LUNARKN1GHT/CampusFlow/issues/69) | 待认领 | S | #48（W004） |
| [U003 · 实现登录退出与受保护页面](https://github.com/LUNARKN1GHT/CampusFlow/issues/70) | 待认领 | S | #48（W004）, #68（U001）, #69（U002） |
| [U004 · 实现学期列表创建与归档恢复界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/71) | 待认领 | S | #53（W009）, #69（U002） |
| [U005 · 实现课程列表与编辑表单](https://github.com/LUNARKN1GHT/CampusFlow/issues/72) | 待认领 | S | #56（W010）, #71（U004） |
| [U006 · 实现任务列表与手动编辑表单](https://github.com/LUNARKN1GHT/CampusFlow/issues/73) | 待认领 | M | #62（T007）, #63（T008）, #64（T009）, #65（T010）, #72（U005） |
| [U007 · 实现任务进度与调整记录界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/74) | 待认领 | S | #61（T006）, #73（U006） |
| [U008 · 实现固定日程录入与周视图](https://github.com/LUNARKN1GHT/CampusFlow/issues/75) | 待认领 | M | #66（T011）, #69（U002） |
| [U009 · 实现时区、可用时间和学习偏好设置页](https://github.com/LUNARKN1GHT/CampusFlow/issues/76) | 待认领 | S | #67（T012）, #51（W007）, #69（U002） |
| [U010 · 验收手动管理端到端流程](https://github.com/LUNARKN1GHT/CampusFlow/issues/77) | 待认领 | S | #70（U003）, #72（U005）, #74（U007）, #75（U008）, #76（U009） |

## M2 · 资料导入与来源定位

### [[E20] 资料存储与生命周期](https://github.com/LUNARKN1GHT/CampusFlow/issues/8)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [D001 · 建立资料、版本与来源元数据模型](https://github.com/LUNARKN1GHT/CampusFlow/issues/78) | 待认领 | S | #50（W006）, #42（B014） |
| [D002 · 实现私有原文件存储适配器](https://github.com/LUNARKN1GHT/CampusFlow/issues/79) | 待认领 | S | #78（D001） |
| [D003 · 实现 PDF 与图片上传校验](https://github.com/LUNARKN1GHT/CampusFlow/issues/80) | 待认领 | S | #79（D002） |
| [D004 · 实现粘贴文本导入](https://github.com/LUNARKN1GHT/CampusFlow/issues/81) | 待认领 | S | #78（D001） |
| [D005 · 实现资料列表筛选与元数据修订](https://github.com/LUNARKN1GHT/CampusFlow/issues/82) | 待认领 | S | #78（D001） |
| [D006 · 检测完全相同文件的重复导入](https://github.com/LUNARKN1GHT/CampusFlow/issues/83) | 待认领 | S | #80（D003） |
| [D007 · 实现资料归档与恢复](https://github.com/LUNARKN1GHT/CampusFlow/issues/84) | 待认领 | S | #82（D005） |
| [D008 · 实现资料删除影响预览与确认](https://github.com/LUNARKN1GHT/CampusFlow/issues/85) | 待认领 | S | #82（D005） |
| [D009 · 实现确认后的资料删除与派生失效](https://github.com/LUNARKN1GHT/CampusFlow/issues/92) | 待认领 | M | #85（D008）, #91（J007） |
| [D010 · 说明备份保留与恢复后的删除策略](https://github.com/LUNARKN1GHT/CampusFlow/issues/93) | 待认领 | S | #85（D008） |

### [[E21] PDF、图片解析与原文定位](https://github.com/LUNARKN1GHT/CampusFlow/issues/9)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [D011 · 定义可替换解析器输出协议](https://github.com/LUNARKN1GHT/CampusFlow/issues/87) | 待认领 | S | #78（D001）, #38（B010）, #39（B011） |
| [D012 · 实现 pypdf 文本页解析](https://github.com/LUNARKN1GHT/CampusFlow/issues/94) | 待认领 | S | #87（D011） |
| [D013 · 评估并选择 OCR 或视觉识别适配器](https://github.com/LUNARKN1GHT/CampusFlow/issues/95) | 待认领 | S | #87（D011）, #39（B011） |
| [D014 · 实现图片 OCR 或视觉识别适配器](https://github.com/LUNARKN1GHT/CampusFlow/issues/96) | 待认领 | M | #95（D013） |
| [D015 · 为扫描 PDF 增加逐页识别回退](https://github.com/LUNARKN1GHT/CampusFlow/issues/97) | 待认领 | S | #94（D012）, #96（D014） |
| [D016 · 处理表格和跨页续表的来源关联](https://github.com/LUNARKN1GHT/CampusFlow/issues/98) | 待认领 | M | #94（D012）, #96（D014）, #38（B010） |
| [D017 · 持久化来源片段与定位坐标](https://github.com/LUNARKN1GHT/CampusFlow/issues/99) | 待认领 | S | #87（D011） |
| [D018 · 实现授权原文读取与定位接口](https://github.com/LUNARKN1GHT/CampusFlow/issues/100) | 待认领 | S | #79（D002）, #99（D017）, #50（W006） |
| [D019 · 实现资料库上传与处理状态界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/103) | 待认领 | M | #80（D003）, #81（D004）, #82（D005）, #102（J006）, #69（U002） |
| [D020 · 实现 PDF、图片和文本原文定位视图](https://github.com/LUNARKN1GHT/CampusFlow/issues/104) | 待认领 | M | #100（D018）, #69（U002） |
| [D021 · 实现资料归档删除交互](https://github.com/LUNARKN1GHT/CampusFlow/issues/105) | 待认领 | S | #84（D007）, #92（D009）, #93（D010）, #103（D019） |

### [[E22] 异步处理与可恢复作业](https://github.com/LUNARKN1GHT/CampusFlow/issues/10)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [J001 · 建立处理作业状态与尝试记录模型](https://github.com/LUNARKN1GHT/CampusFlow/issues/86) | 待认领 | S | #78（D001） |
| [J002 · 接入 RQ 与独立 Worker 启动入口](https://github.com/LUNARKN1GHT/CampusFlow/issues/88) | 待认领 | S | #86（J001）, #87（D011） |
| [J003 · 实现资料和待投递记录同事务保存](https://github.com/LUNARKN1GHT/CampusFlow/issues/89) | 待认领 | S | #88（J002） |
| [J004 · 实现待投递补偿与作业幂等领取](https://github.com/LUNARKN1GHT/CampusFlow/issues/90) | 待认领 | S | #89（J003） |
| [J005 · 实现分阶段失败重试与超时处理](https://github.com/LUNARKN1GHT/CampusFlow/issues/101) | 待认领 | S | #90（J004） |
| [J006 · 提供作业状态查询与失败重试 API](https://github.com/LUNARKN1GHT/CampusFlow/issues/102) | 待认领 | S | #101（J005）, #50（W006） |
| [J007 · 阻止删除或过期版本的作业回写](https://github.com/LUNARKN1GHT/CampusFlow/issues/91) | 待认领 | S | #90（J004）, #78（D001） |
| [J008 · 验证队列断连、重复消息和部分失败恢复](https://github.com/LUNARKN1GHT/CampusFlow/issues/106) | 待认领 | M | #101（J005）, #91（J007）, #97（D015）, #99（D017） |
| [J009 · 补全资料处理运行文档与诊断入口](https://github.com/LUNARKN1GHT/CampusFlow/issues/107) | 待认领 | S | #102（J006）, #103（D019） |

## M3 · 事项提取与人工核对

### [[E30] 候选事实提取与不确定性](https://github.com/LUNARKN1GHT/CampusFlow/issues/11)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [X001 · 定义候选事项与字段证据模型](https://github.com/LUNARKN1GHT/CampusFlow/issues/108) | 待认领 | S | #99（D017） |
| [X002 · 建立模型生成适配接口与结构化校验](https://github.com/LUNARKN1GHT/CampusFlow/issues/109) | 待认领 | S | #108（X001）, #95（D013） |
| [X003 · 实现任务要求与 DDL 候选提取](https://github.com/LUNARKN1GHT/CampusFlow/issues/110) | 待认领 | S | #109（X002）, #38（B010）, #40（B012） |
| [X004 · 实现固定日程候选提取](https://github.com/LUNARKN1GHT/CampusFlow/issues/111) | 待认领 | S | #109（X002） |
| [X005 · 实现课程规则候选提取](https://github.com/LUNARKN1GHT/CampusFlow/issues/112) | 待认领 | S | #109（X002） |
| [X006 · 实现有依据的相对日期换算](https://github.com/LUNARKN1GHT/CampusFlow/issues/113) | 待认领 | M | #110（X003）, #40（B012） |
| [X007 · 实现课程教学班匹配与歧义标记](https://github.com/LUNARKN1GHT/CampusFlow/issues/114) | 待认领 | S | #108（X001）, #46（W002） |
| [X008 · 识别截图截断和来源矛盾的待核对原因](https://github.com/LUNARKN1GHT/CampusFlow/issues/115) | 待认领 | S | #108（X001）, #87（D011）, #39（B011）, #40（B012） |
| [X009 · 为资料内指令建立执行隔离回归](https://github.com/LUNARKN1GHT/CampusFlow/issues/116) | 待认领 | S | #109（X002） |
| [X010 · 评估候选提取与不确定信息识别](https://github.com/LUNARKN1GHT/CampusFlow/issues/117) | 待认领 | S | #110（X003）, #111（X004）, #112（X005）, #113（X006）, #114（X007）, #115（X008）, #45（B017） |

### [[E31] 集中核对与正式事项生成](https://github.com/LUNARKN1GHT/CampusFlow/issues/12)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [R001 · 提供待核对事项列表与原因筛选](https://github.com/LUNARKN1GHT/CampusFlow/issues/118) | 待认领 | S | #108（X001）, #115（X008） |
| [R002 · 实现候选字段修正与用户补充记录](https://github.com/LUNARKN1GHT/CampusFlow/issues/119) | 待认领 | S | #108（X001） |
| [R003 · 实现候选确认、忽略和失效状态转换](https://github.com/LUNARKN1GHT/CampusFlow/issues/120) | 待认领 | S | #119（R002） |
| [R004 · 将确认的任务转换为正式任务](https://github.com/LUNARKN1GHT/CampusFlow/issues/121) | 待认领 | S | #120（R003）, #54（T001） |
| [R005 · 将确认的日程转换为固定日程](https://github.com/LUNARKN1GHT/CampusFlow/issues/122) | 待认领 | S | #120（R003）, #55（T002） |
| [R006 · 保存确认规则并提供规则查询](https://github.com/LUNARKN1GHT/CampusFlow/issues/123) | 待认领 | S | #120（R003）, #112（X005） |
| [R007 · 建立任务与日程的来源详情接口](https://github.com/LUNARKN1GHT/CampusFlow/issues/124) | 待认领 | S | #121（R004）, #122（R005）, #92（D009） |
| [R008 · 实现集中核对列表与详情界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/125) | 待认领 | M | #118（R001）, #119（R002）, #120（R003）, #104（D020）, #69（U002） |
| [R009 · 实现逐字段修正和确认结果反馈](https://github.com/LUNARKN1GHT/CampusFlow/issues/126) | 待认领 | S | #121（R004）, #122（R005）, #123（R006）, #125（R008） |
| [R010 · 验收资料到正式事项的完整链路](https://github.com/LUNARKN1GHT/CampusFlow/issues/127) | 待认领 | M | #124（R007）, #126（R009）, #117（X010） |

## M4 · 带引用的问答

### [[E40] 检索索引与模型适配](https://github.com/LUNARKN1GHT/CampusFlow/issues/13)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [Q001 · 评估中文检索与生成模型组合](https://github.com/LUNARKN1GHT/CampusFlow/issues/128) | 待认领 | S | #45（B017）, #117（X010） |
| [Q002 · 实现 embedding 适配器和版本记录](https://github.com/LUNARKN1GHT/CampusFlow/issues/129) | 待认领 | S | #128（Q001）, #99（D017） |
| [Q003 · 实现保留来源位置的文本分块](https://github.com/LUNARKN1GHT/CampusFlow/issues/130) | 待认领 | S | #99（D017）, #128（Q001） |
| [Q004 · 建立 pgvector 索引与异步索引流程](https://github.com/LUNARKN1GHT/CampusFlow/issues/131) | 待认领 | S | #129（Q002）, #130（Q003）, #90（J004） |
| [Q005 · 实现工作空间和适用范围前置过滤](https://github.com/LUNARKN1GHT/CampusFlow/issues/132) | 待认领 | S | #131（Q004）, #50（W006） |
| [Q006 · 实现关键词与向量检索基线比较](https://github.com/LUNARKN1GHT/CampusFlow/issues/133) | 待认领 | S | #132（Q005）, #128（Q001） |
| [Q007 · 建立稳定引用 ID 与候选集校验](https://github.com/LUNARKN1GHT/CampusFlow/issues/134) | 待认领 | S | #132（Q005） |
| [Q008 · 在删除归档和范围变更时失效索引](https://github.com/LUNARKN1GHT/CampusFlow/issues/135) | 待认领 | S | #131（Q004）, #84（D007）, #92（D009）, #91（J007） |
| [Q009 · 增加模型超时、限额与失败降级](https://github.com/LUNARKN1GHT/CampusFlow/issues/136) | 待认领 | S | #129（Q002）, #109（X002） |
| [Q010 · 建立检索召回与引用支持评估集](https://github.com/LUNARKN1GHT/CampusFlow/issues/137) | 待认领 | S | #133（Q006）, #134（Q007）, #135（Q008）, #45（B017） |

### [[E41] 可追溯问答与交互](https://github.com/LUNARKN1GHT/CampusFlow/issues/14)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [Q011 · 实现问答用例与请求响应协议](https://github.com/LUNARKN1GHT/CampusFlow/issues/141) | 待认领 | S | #134（Q007）, #136（Q009）, #123（R006） |
| [Q012 · 实现资料不足和部分覆盖的回答策略](https://github.com/LUNARKN1GHT/CampusFlow/issues/146) | 待认领 | S | #141（Q011）, #87（D011） |
| [Q013 · 实现多资料对照与矛盾展示](https://github.com/LUNARKN1GHT/CampusFlow/issues/147) | 待认领 | S | #141（Q011）, #115（X008） |
| [Q014 · 实现有范围的连续追问上下文](https://github.com/LUNARKN1GHT/CampusFlow/issues/148) | 待认领 | S | #141（Q011）, #135（Q008） |
| [Q015 · 实现查询、规划、冲突检查的意图协议](https://github.com/LUNARKN1GHT/CampusFlow/issues/149) | 待认领 | S | #141（Q011） |
| [Q016 · 实现问答页面和引用卡片](https://github.com/LUNARKN1GHT/CampusFlow/issues/151) | 待认领 | M | #146（Q012）, #147（Q013）, #148（Q014）, #104（D020）, #69（U002） |
| [Q017 · 实现来源失效与回答覆盖提示](https://github.com/LUNARKN1GHT/CampusFlow/issues/156) | 待认领 | S | #135（Q008）, #151（Q016）, #125（R008） |
| [Q018 · 验收 DDL 回答与引用语义一致](https://github.com/LUNARKN1GHT/CampusFlow/issues/161) | 待认领 | M | #137（Q010）, #156（Q017）, #41（B013） |

## M5 · 两周计划与 MVP 验收

### [[E50] 两周排期核心与输入校验](https://github.com/LUNARKN1GHT/CampusFlow/issues/15)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [P001 · 建立计划草案、版本与时间块模型](https://github.com/LUNARKN1GHT/CampusFlow/issues/138) | 待认领 | S | #62（T007）, #64（T009） |
| [P002 · 汇总未来两周的任务与时间约束](https://github.com/LUNARKN1GHT/CampusFlow/issues/142) | 待认领 | S | #138（P001）, #66（T011）, #67（T012）, #51（W007） |
| [P003 · 识别缺失日期耗时与待核对约束](https://github.com/LUNARKN1GHT/CampusFlow/issues/150) | 待认领 | S | #142（P002）, #120（R003） |
| [P004 · 计算扣除固定日程后的可用时间片](https://github.com/LUNARKN1GHT/CampusFlow/issues/152) | 待认领 | S | #142（P002） |
| [P005 · 实现截止约束下的任务时间分配](https://github.com/LUNARKN1GHT/CampusFlow/issues/157) | 待认领 | M | #150（P003）, #152（P004） |
| [P006 · 实现优先级、学习容量与休息偏好](https://github.com/LUNARKN1GHT/CampusFlow/issues/162) | 待认领 | S | #157（P005）, #51（W007） |
| [P007 · 支持可编辑任务分段与提交缓冲](https://github.com/LUNARKN1GHT/CampusFlow/issues/166) | 待认领 | S | #162（P006） |
| [P008 · 输出未排任务、容量缺口和调整选项](https://github.com/LUNARKN1GHT/CampusFlow/issues/171) | 待认领 | S | #157（P005）, #166（P007） |
| [P009 · 生成时间块安排理由与约束来源](https://github.com/LUNARKN1GHT/CampusFlow/issues/176) | 待认领 | S | #171（P008）, #124（R007） |
| [P010 · 验证排期边界与不可行输入](https://github.com/LUNARKN1GHT/CampusFlow/issues/181) | 待认领 | M | #176（P009）, #41（B013） |

### [[E51] 冲突检查、计划接受与调整](https://github.com/LUNARKN1GHT/CampusFlow/issues/16)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [P011 · 检测固定日程及学习时间块重叠](https://github.com/LUNARKN1GHT/CampusFlow/issues/158) | 待认领 | S | #152（P004）, #138（P001） |
| [P012 · 计算截止风险与学习负荷风险](https://github.com/LUNARKN1GHT/CampusFlow/issues/177) | 待认领 | S | #171（P008）, #64（T009） |
| [P013 · 实现草案生成查询与版本化接受 API](https://github.com/LUNARKN1GHT/CampusFlow/issues/186) | 待认领 | S | #138（P001）, #181（P010） |
| [P014 · 支持手动移动和锁定已接受时间块](https://github.com/LUNARKN1GHT/CampusFlow/issues/191) | 待认领 | S | #186（P013）, #158（P011） |
| [P015 · 实现两周草案与当前计划界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/192) | 待认领 | M | #176（P009）, #177（P012）, #186（P013）, #73（U006）, #75（U008） |
| [P016 · 实现冲突详情、时间块调整与锁定界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/196) | 待认领 | S | #191（P014）, #192（P015） |
| [P017 · 接通规划与冲突检查自然语言入口](https://github.com/LUNARKN1GHT/CampusFlow/issues/193) | 待认领 | S | #149（Q015）, #186（P013）, #177（P012） |
| [P018 · 实现近期任务与今日安排总览](https://github.com/LUNARKN1GHT/CampusFlow/issues/197) | 待认领 | S | #192（P015）, #125（R008）, #151（Q016） |

### [[E52] MVP 集成验收与发布准备](https://github.com/LUNARKN1GHT/CampusFlow/issues/17)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [V001 · 执行八项 MVP 端到端验收](https://github.com/LUNARKN1GHT/CampusFlow/issues/201) | 待认领 | M | #41（B013）, #127（R010）, #161（Q018）, #181（P010）, #196（P016） |
| [V002 · 验证空间隔离与资料内指令边界](https://github.com/LUNARKN1GHT/CampusFlow/issues/194) | 待认领 | M | #50（W006）, #100（D018）, #132（Q005）, #186（P013）, #116（X009） |
| [V003 · 验证删除与运行中处理的端到端竞态](https://github.com/LUNARKN1GHT/CampusFlow/issues/139) | 待认领 | S | #105（D021）, #135（Q008）, #127（R010）, #106（J008） |
| [V004 · 完成 MVP 页面易用性与无障碍走查](https://github.com/LUNARKN1GHT/CampusFlow/issues/202) | 待认领 | S | #197（P018）, #156（Q017）, #105（D021） |
| [V005 · 测量典型样本处理时延与模型成本](https://github.com/LUNARKN1GHT/CampusFlow/issues/206) | 待认领 | S | #201（V001）, #45（B017） |
| [V006 · 完善本地整套启动与故障恢复说明](https://github.com/LUNARKN1GHT/CampusFlow/issues/198) | 待认领 | S | #107（J009）, #193（P017） |
| [V007 · 准备 MVP 发布说明与候选版本验收](https://github.com/LUNARKN1GHT/CampusFlow/issues/211) | 待认领 | S | #201（V001）, #194（V002）, #139（V003）, #202（V004）, #206（V005）, #198（V006） |

## M6 · 扩展导入与通知变更

### [[E60] Word 与公开网页导入](https://github.com/LUNARKN1GHT/CampusFlow/issues/18)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [N001 · 制作 Word 与公开网页验收样本](https://github.com/LUNARKN1GHT/CampusFlow/issues/140) | 待认领 | S | #37（B009） |
| [N002 · 实现 Word 格式与大小校验](https://github.com/LUNARKN1GHT/CampusFlow/issues/143) | 待认领 | S | #80（D003）, #140（N001） |
| [N003 · 实现 Word 段落表格解析及定位](https://github.com/LUNARKN1GHT/CampusFlow/issues/153) | 待认领 | M | #143（N002）, #87（D011） |
| [N004 · 实现公开网页抓取的目标校验](https://github.com/LUNARKN1GHT/CampusFlow/issues/144) | 待认领 | S | #140（N001）, #50（W006） |
| [N005 · 保存网页快照与发布来源](https://github.com/LUNARKN1GHT/CampusFlow/issues/154) | 待认领 | S | #144（N004）, #78（D001） |
| [N006 · 实现网页正文提取与段落定位](https://github.com/LUNARKN1GHT/CampusFlow/issues/159) | 待认领 | S | #154（N005）, #87（D011） |
| [N007 · 展示网页不可访问时的替代输入](https://github.com/LUNARKN1GHT/CampusFlow/issues/163) | 待认领 | S | #159（N006）, #81（D004） |
| [N008 · 接通 Word 与网页导入界面和作业流程](https://github.com/LUNARKN1GHT/CampusFlow/issues/167) | 待认领 | M | #153（N003）, #159（N006）, #163（N007）, #102（J006）, #103（D019） |
| [N009 · 验收扩展输入到引用问答的链路](https://github.com/LUNARKN1GHT/CampusFlow/issues/172) | 待认领 | S | #167（N008）, #161（Q018） |

### [[E61] 资料版本、通知差异与重复合并](https://github.com/LUNARKN1GHT/CampusFlow/issues/19)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [N010 · 实现资料版本关联与替代声明记录](https://github.com/LUNARKN1GHT/CampusFlow/issues/145) | 待认领 | S | #78（D001）, #123（R006） |
| [N011 · 生成日期、地点、要求与范围字段差异](https://github.com/LUNARKN1GHT/CampusFlow/issues/155) | 待认领 | S | #145（N010）, #108（X001） |
| [N012 · 判定版本适用性与待核对冲突](https://github.com/LUNARKN1GHT/CampusFlow/issues/160) | 待认领 | S | #155（N011） |
| [N013 · 查询通知变更影响的任务与时间块](https://github.com/LUNARKN1GHT/CampusFlow/issues/164) | 待认领 | S | #155（N011）, #124（R007）, #138（P001） |
| [N014 · 实现版本接受与变更历史](https://github.com/LUNARKN1GHT/CampusFlow/issues/168) | 待认领 | S | #160（N012）, #164（N013） |
| [N015 · 识别重复候选任务与日程](https://github.com/LUNARKN1GHT/CampusFlow/issues/165) | 待认领 | S | #108（X001）, #121（R004）, #145（N010） |
| [N016 · 实现重复事项合并与关联迁移](https://github.com/LUNARKN1GHT/CampusFlow/issues/169) | 待认领 | M | #165（N015）, #61（T006） |
| [N017 · 实现版本对照与变更接受界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/173) | 待认领 | M | #168（N014）, #104（D020） |
| [N018 · 实现重复建议与合并预览界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/174) | 待认领 | S | #169（N016）, #125（R008） |
| [N019 · 验证通知冲突与变更历史不被覆盖](https://github.com/LUNARKN1GHT/CampusFlow/issues/178) | 待认领 | S | #173（N017）, #174（N018）, #40（B012） |

## M7 · 日常执行与局部调整

### [[E70] 依赖任务、分解与局部重排](https://github.com/LUNARKN1GHT/CampusFlow/issues/20)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [A001 · 建立任务依赖与子任务模型](https://github.com/LUNARKN1GHT/CampusFlow/issues/170) | 待认领 | S | #61（T006）, #64（T009） |
| [A002 · 校验任务依赖成环和缺失前置](https://github.com/LUNARKN1GHT/CampusFlow/issues/175) | 待认领 | S | #170（A001） |
| [A003 · 在排期中满足必要任务依赖](https://github.com/LUNARKN1GHT/CampusFlow/issues/179) | 待认领 | M | #175（A002）, #157（P005） |
| [A004 · 实现可编辑子任务与耗时汇总](https://github.com/LUNARKN1GHT/CampusFlow/issues/180) | 待认领 | S | #170（A001）, #64（T009） |
| [A005 · 实现保留锁定时间块的局部重排](https://github.com/LUNARKN1GHT/CampusFlow/issues/199) | 待认领 | M | #179（A003）, #191（P014） |
| [A006 · 根据通知、进度或可用时间变化生成调整建议](https://github.com/LUNARKN1GHT/CampusFlow/issues/203) | 待认领 | S | #199（A005）, #168（N014）, #64（T009）, #51（W007） |
| [A007 · 增加今天、本周与自定义规划区间](https://github.com/LUNARKN1GHT/CampusFlow/issues/204) | 待认领 | S | #199（A005） |
| [A008 · 实现依赖、子任务与局部调整界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/207) | 待认领 | M | #180（A004）, #203（A006）, #204（A007）, #196（P016） |
| [A009 · 验证依赖排期与局部重排不变量](https://github.com/LUNARKN1GHT/CampusFlow/issues/212) | 待认领 | S | #207（A008）, #178（N019） |

### [[E71] 应用内提醒](https://github.com/LUNARKN1GHT/CampusFlow/issues/21)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [A010 · 建立提醒规则与触发记录模型](https://github.com/LUNARKN1GHT/CampusFlow/issues/182) | 待认领 | S | #61（T006）, #138（P001） |
| [A011 · 实现近期截止与待核对提醒生成](https://github.com/LUNARKN1GHT/CampusFlow/issues/187) | 待认领 | S | #182（A010）, #120（R003）, #63（T008） |
| [A012 · 实现提醒幂等与重复调度保护](https://github.com/LUNARKN1GHT/CampusFlow/issues/195) | 待认领 | S | #187（A011）, #90（J004） |
| [A013 · 处理完成、取消、暂停和重新打开](https://github.com/LUNARKN1GHT/CampusFlow/issues/200) | 待认领 | S | #195（A012）, #61（T006） |
| [A014 · 在计划通知变化后重算受影响提醒](https://github.com/LUNARKN1GHT/CampusFlow/issues/208) | 待认领 | S | #200（A013）, #203（A006）, #168（N014） |
| [A015 · 实现提醒中心及规则设置界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/213) | 待认领 | M | #208（A014）, #197（P018） |
| [A016 · 验证提醒漏发与重复边界](https://github.com/LUNARKN1GHT/CampusFlow/issues/216) | 待认领 | S | #213（A015） |

### [[E72] 清单、日历文件与第二阶段验收](https://github.com/LUNARKN1GHT/CampusFlow/issues/22)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [A017 · 定义清单与日历导出范围和字段](https://github.com/LUNARKN1GHT/CampusFlow/issues/205) | 待认领 | XS | #62（T007）, #186（P013） |
| [A018 · 实现任务清单导出](https://github.com/LUNARKN1GHT/CampusFlow/issues/209) | 待认领 | S | #205（A017） |
| [A019 · 实现日历草案 ICS 导出](https://github.com/LUNARKN1GHT/CampusFlow/issues/210) | 待认领 | S | #205（A017）, #66（T011）, #52（W008） |
| [A020 · 实现导出预览与下载界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/214) | 待认领 | S | #209（A018）, #210（A019） |
| [A021 · 验证导出文件的日期和时区互操作](https://github.com/LUNARKN1GHT/CampusFlow/issues/217) | 待认领 | S | #214（A020） |
| [A022 · 执行第二阶段端到端验收与发布记录](https://github.com/LUNARKN1GHT/CampusFlow/issues/221) | 待认领 | M | #172（N009）, #178（N019）, #212（A009）, #216（A016）, #217（A021） |

## M8 · 课程关系与修读路径

### [[E80] 培养方案与课程关系数据](https://github.com/LUNARKN1GHT/CampusFlow/issues/23)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [C001 · 制作培养方案与课程依赖验收样本](https://github.com/LUNARKN1GHT/CampusFlow/issues/183) | 待认领 | S | #37（B009） |
| [C002 · 建立专业年级与培养方案版本模型](https://github.com/LUNARKN1GHT/CampusFlow/issues/184) | 待认领 | S | #51（W007）, #78（D001） |
| [C003 · 实现培养方案课程和条件候选提取](https://github.com/LUNARKN1GHT/CampusFlow/issues/188) | 待认领 | M | #183（C001）, #184（C002）, #109（X002） |
| [C004 · 分别建模强制先修、并修与建议先学](https://github.com/LUNARKN1GHT/CampusFlow/issues/189) | 待认领 | S | #184（C002）, #108（X001） |
| [C005 · 实现课程关系人工核对与确认](https://github.com/LUNARKN1GHT/CampusFlow/issues/215) | 待认领 | S | #188（C003）, #189（C004）, #120（R003） |
| [C006 · 实现课程别名与唯一匹配规则](https://github.com/LUNARKN1GHT/CampusFlow/issues/190) | 待认领 | S | #184（C002）, #114（X007） |
| [C007 · 实现已修、在修与待修状态维护](https://github.com/LUNARKN1GHT/CampusFlow/issues/218) | 待认领 | S | #184（C002）, #46（W002） |
| [C008 · 实现个人方案、别名和修读状态界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/222) | 待认领 | M | #215（C005）, #190（C006）, #218（C007）, #72（U005） |

### [[E81] 课程依赖图与修读辅助](https://github.com/LUNARKN1GHT/CampusFlow/issues/24)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [C009 · 构建适用方案内的课程关系图](https://github.com/LUNARKN1GHT/CampusFlow/issues/223) | 待认领 | S | #215（C005）, #218（C007） |
| [C010 · 检测强制先修环并返回具体路径](https://github.com/LUNARKN1GHT/CampusFlow/issues/225) | 待认领 | S | #223（C009） |
| [C011 · 检测缺失课程和来源矛盾](https://github.com/LUNARKN1GHT/CampusFlow/issues/226) | 待认领 | S | #223（C009）, #190（C006） |
| [C012 · 对合法强制先修图进行拓扑排序](https://github.com/LUNARKN1GHT/CampusFlow/issues/227) | 待认领 | S | #225（C010）, #226（C011） |
| [C013 · 根据修读状态展示下一步可学课程](https://github.com/LUNARKN1GHT/CampusFlow/issues/229) | 待认领 | S | #227（C012）, #218（C007） |
| [C014 · 根据已知开课学期和学分限制辅助排学期](https://github.com/LUNARKN1GHT/CampusFlow/issues/230) | 待认领 | M | #229（C013）, #188（C003） |
| [C015 · 实现课程关系图与证据查看界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/228) | 待认领 | M | #225（C010）, #226（C011）, #104（D020）, #222（C008） |
| [C016 · 实现修读顺序与学期辅助界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/231) | 待认领 | S | #230（C014）, #228（C015） |
| [C017 · 验收课程成环缺项与范围隔离](https://github.com/LUNARKN1GHT/CampusFlow/issues/232) | 待认领 | M | #183（C001）, #231（C016） |

## M9 · 外部日历与完整流程验收

### [[E90] 日历授权与操作确认](https://github.com/LUNARKN1GHT/CampusFlow/issues/25)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [K001 · 评估首个外部日历供应商与连接范围](https://github.com/LUNARKN1GHT/CampusFlow/issues/233) | 待认领 | S | #210（A019）, #232（C017） |
| [K002 · 建立日历连接与凭据存储接口](https://github.com/LUNARKN1GHT/CampusFlow/issues/234) | 待认领 | S | #233（K001）, #50（W006） |
| [K003 · 实现授权回调、过期与撤销流程](https://github.com/LUNARKN1GHT/CampusFlow/issues/235) | 待认领 | M | #234（K002） |
| [K004 · 建立外部操作草案与确认版本模型](https://github.com/LUNARKN1GHT/CampusFlow/issues/236) | 待认领 | S | #234（K002）, #138（P001） |
| [K005 · 实现外部写入影响预览](https://github.com/LUNARKN1GHT/CampusFlow/issues/237) | 待认领 | S | #236（K004）, #210（A019） |
| [K006 · 校验操作确认与最新内容完全一致](https://github.com/LUNARKN1GHT/CampusFlow/issues/239) | 待认领 | S | #237（K005）, #235（K003） |
| [K007 · 实现日历连接与逐项确认界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/240) | 待认领 | M | #235（K003）, #237（K005）, #239（K006）, #69（U002） |

### [[E91] 日历执行可靠性与完整流程验收](https://github.com/LUNARKN1GHT/CampusFlow/issues/26)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [K008 · 建立外部执行记录与幂等标识](https://github.com/LUNARKN1GHT/CampusFlow/issues/238) | 待认领 | S | #236（K004） |
| [K009 · 实现已确认日历事件新增适配器](https://github.com/LUNARKN1GHT/CampusFlow/issues/241) | 待认领 | S | #239（K006）, #238（K008） |
| [K010 · 实现外部事件修改和删除适配器](https://github.com/LUNARKN1GHT/CampusFlow/issues/242) | 待认领 | S | #241（K009） |
| [K011 · 处理超时后的未知结果与安全重试](https://github.com/LUNARKN1GHT/CampusFlow/issues/243) | 待认领 | M | #241（K009） |
| [K012 · 实现批量逐项结果与只重试失败项](https://github.com/LUNARKN1GHT/CampusFlow/issues/244) | 待认领 | S | #242（K010）, #243（K011） |
| [K013 · 处理通知和计划变化引起的同步差异](https://github.com/LUNARKN1GHT/CampusFlow/issues/245) | 待认领 | S | #244（K012）, #168（N014）, #203（A006） |
| [K014 · 实现同步结果、失败重试和撤销提示界面](https://github.com/LUNARKN1GHT/CampusFlow/issues/246) | 待认领 | S | #244（K012）, #245（K013）, #240（K007） |
| [K015 · 验收确认范围、撤销、超时和幂等](https://github.com/LUNARKN1GHT/CampusFlow/issues/247) | 待认领 | M | #246（K014） |
| [K016 · 执行三阶段完整回归与发布检查](https://github.com/LUNARKN1GHT/CampusFlow/issues/248) | 待认领 | M | #211（V007）, #221（A022）, #232（C017）, #247（K015） |

## 后续探索（无交付 Milestone）

### [[EF0] 后续探索：自动获取、多设备与协作](https://github.com/LUNARKN1GHT/CampusFlow/issues/27)

| 任务 | 初始状态 | 规模 | 前置任务 |
| --- | --- | --- | --- |
| [F001 · 评估自动通知更新的来源与授权边界](https://github.com/LUNARKN1GHT/CampusFlow/issues/185) | 待认领 | S | 无 |
| [F002 · 评估多设备访问与数据一致性需求](https://github.com/LUNARKN1GHT/CampusFlow/issues/219) | 待认领 | S | 无 |
| [F003 · 评估协作共享和角色权限范围](https://github.com/LUNARKN1GHT/CampusFlow/issues/220) | 待认领 | S | 无 |
| [F004 · 评估其他校内系统连接需求](https://github.com/LUNARKN1GHT/CampusFlow/issues/224) | 待认领 | S | 无 |
| [既有 #2：优化查询方式](https://github.com/LUNARKN1GHT/CampusFlow/issues/2) | 待范围决策 | 待评估 | 不纳入前三阶段 |

