# Q001 真实比较：进度与恢复点

2026-10-15，负责人金嘉睿，分支 `issue/#128`，关联 PR #378。本记录用于中断后继续，不表示真实评测已完成。

## 已确认选择

- 沿用智谱，仅发送项目合成样本，本轮累计模型调用上限 **1 元**。
- 四组：Embedding-2／Embedding-3 各搭配 GLM-4-Flash-250414／GLM-4.5-Flash。
- 两个检索模型都使用 1024 维；“维”即每段文字转换后的数字个数，便于控制存储规模和比较条件。
- 已有离线分数是预设自检，不得混入真实分数；人工复核未完成前不宣称引用语义支持通过，不关闭 Q001。

## 当前保存点

- 人工标注复核表导出已实现；专项测试 30 项通过。新运行会输出 `annotation-review.csv`，初始均 pending，复核人／日期为空，不替评审者签字。UTF-8 BOM 便于 Windows 表格软件显示中文，可能被当公式的文本按文字导出。
- 四个型号的官方资料已读取；真实调用尚未开始。下一步实现独立真实运行器、持久保存每次调用的预算和结果、增加离线网络替身测试，然后才运行。

## 官方依据（2026-10-15 核实）

- [Embedding-2](https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-2)：0.5 元／百万 Tokens，固定 1024 维。
- [Embedding-3](https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-3)：0.5 元／百万 Tokens，允许 1024 维。
- [文本向量接口](https://docs.bigmodel.cn/api-reference/模型-api/文本嵌入)：固定使用官方 embeddings 地址；两种型号输入限制不同，运行器使用短文本逐条请求并检查返回长度和数值。
- [GLM-4-Flash-250414](https://docs.bigmodel.cn/cn/guide/models/free/glm-4-flash-250414) 与 [GLM-4.5-Flash](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.5-flash)：型号页当前均标明免费。后者可关闭深度思考；比较时保留该配置。
- [官方价格说明](https://docs.bigmodel.cn/cn/guide/start/pricing)：按型号与输入／输出计费，后续重跑需重新核实价格。本文不是永久价格承诺。
- [隐私政策](https://docs.bigmodel.cn/cn/terms/privacy-policy)／[服务协议](https://docs.bigmodel.cn/cn/terms/service-agreement)：平台个人信息存储说明为境内、必要期限，政策也涉及匿名数据用于服务改进；没有据此获得 API 请求零留存或绝不训练的承诺。本轮不上传真实课程／个人资料；具体 API 保留天数与账号专属条款仍待供应商澄清。

## 后续检查清单

1. 保持预期答案只进入评分／复核资料，不能发送给模型；同一范围候选较少，需在比较结论中明确检索难度限制。
2. 在请求前预留费用并保存；超时或缺少用量时保留预留额并停止，不以“没有返回结果”认定免费。禁止自动重试，不切换未选型号。
3. 逐条保存调用结果、用量、耗时、失败和人工复核待办，失败／中断不丢已有结果，不重复扣费重跑。
4. 本地测试通过后执行一次授权小样本比较；模型输出按原文人工核对，结果先待复核，保留服务失败和样本不足的限制。
5. 及时以金嘉睿身份提交并推送。PR 保持 Q001 的部分交付范围，不能自动关闭未完成任务。
