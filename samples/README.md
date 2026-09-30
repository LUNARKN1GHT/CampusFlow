# 验收样本管理

本目录只保存 CampusFlow 开发和验收所需的合成样本，或已取得明确许可并完成必要脱敏的资料。任何样本进入仓库前，都必须先在 [registry.csv](registry.csv) 登记；无法确认授权范围的原始资料不得提交。

## 登记流程

1. 为样本分配稳定的 `sample_id`，格式为 `CF-<类型>-<三位序号>`。
2. 在 `registry.csv` 填写来源类型、许可、脱敏方式、保留位置、用途和复核信息。
3. 将样本及其人工预期放在 `samples/synthetic/<任务编号>/` 或团队批准的受控位置。
4. 执行 `python3 scripts/validate_sample_registry.py`，确认路径存在、字段完整且没有重复 ID。
5. 若许可撤回或样本被替换，先将 `status` 改为 `withdrawn`，再移除仓库文件；历史验收记录保留样本 ID，但不得继续声称原文可验证。

## 授权与脱敏规则

- `origin_type=synthetic` 表示完全由项目组构造，不含真实个人或课程资料；许可固定为 `project-internal`。
- 真实资料必须记录许可主体、许可范围和复核日期。公开可访问不等于允许重新分发。
- 脱敏至少检查姓名、学号、联系方式、账号、群名、文件分享链接和可追溯到个人的时间地点组合。
- 样本仅用于登记的 `allowed_uses`，不得跨工作空间、公开发布或用于未说明的模型训练。
- `repository_path` 必须是仓库相对路径；登记表本身和说明文档不作为样本登记。

## 字段说明

| 字段 | 含义 |
| --- | --- |
| `sample_id` | 稳定样本编号，不因重命名而复用 |
| `title` | 不含敏感信息的简短名称 |
| `origin_type` | `synthetic`、`authorized-private` 或 `public-reference` |
| `source_description` | 来源类别或构造方式，不写个人身份 |
| `license_or_permission` | 明确的许可范围或项目内合成声明 |
| `allowed_uses` | 允许的测试、演示或评估用途 |
| `deidentification` | 已执行的脱敏动作；合成样本写 `not-applicable-synthetic` |
| `repository_path` | 样本文件或目录的仓库相对路径 |
| `retention` | 保留期限或删除触发条件 |
| `status` | `active`、`withdrawn` 或 `superseded` |
| `reviewed_by` | 复核人；未指定个人时写角色 |
| `reviewed_on` | ISO 日期 |
| `notes` | 限制、关联预期或替代关系 |

登记只证明样本可以在所列范围内使用，不代表解析或业务验收已经通过。
