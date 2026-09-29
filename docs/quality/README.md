# M0 样本、验收集与原型交付索引

本目录及关联样本完成 Issue #4（E01）的设计与验收准备，不表示 MVP 功能已经实现或八项业务验收已经通过。

## 子任务交付物

| Issue | 交付物 | 验证入口 |
| --- | --- | --- |
| #37 B009 | 样本授权、脱敏与保留规范；登记表 | `python3 scripts/validate_sample_registry.py` |
| #38 B010 | 三页文本 PDF、跨页续表、页码级人工答案 | `samples/synthetic/b010/expected.json` |
| #39 B011 | 清晰、局部模糊、缺页和截断图像；区域级覆盖标注 | `samples/synthetic/b011/expected.json` |
| #40 B012 | 相对日期、缺失日期、同名课程分班和来源冲突文本 | `samples/synthetic/b012/expected.json` |
| #41 B013 | README 八项 MVP 用例及证据格式 | `python3 scripts/validate_mvp_acceptance_cases.py` |
| #42 B014 | 资料导入、完成/部分成功/失败、原文定位原型 | `docs/prototypes/import-source.html` |
| #43 B015 | 集中核对、字段修正、引用和资料不足/冲突问答原型 | `docs/prototypes/review-qa.html` |
| #44 B016 | 计划草案/接受、锁定、未排任务、容量缺口和移动边界原型 | `docs/prototypes/tasks-plan.html` |
| #45 B017 | 提取、引用、不确定性、计划、修改成本、性能与成本指标 | `python3 scripts/validate_evaluation_assets.py` |

所有实际样本及授权信息以 `samples/registry.csv` 为准。原型直接用浏览器打开，不依赖后端、不发送网络请求；其中按钮只用于展示交互层级。

## 集成演示顺序

1. 打开资料导入原型，切换完成、部分成功和失败状态，再查看页码、段落和坐标定位。
2. 打开集中核对原型，查看日期缺失候选，并切换有依据、资料不足和来源冲突问答。
3. 打开两周计划原型，切换草案与已接受计划，核对锁定块、未排任务和 4 小时容量缺口。
4. 对照 `mvp-acceptance-cases.md`，确认以上状态覆盖八项 MVP 场景，但仍需等实现完成后实际执行。
5. 使用 `evaluation-metrics.md` 和 CSV 模板记录后续基线；未实测前不填写性能目标。

## 一致性检查

```bash
python3 scripts/validate_sample_registry.py
python3 scripts/validate_mvp_acceptance_cases.py
python3 scripts/validate_evaluation_assets.py
node --check docs/prototypes/prototype.js
```

PDF 与图像生成器分别位于 `samples/synthetic/b010/` 和 `samples/synthetic/b011/`。重新生成后应执行 `git diff --exit-code`，确保提交的二进制样本可重复生成。
