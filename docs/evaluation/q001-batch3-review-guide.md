# Q001 第三批样本人工复核说明书（跨供应商）

任务：[Q001 #128](https://github.com/LUNARKN1GHT/CampusFlow/issues/128) 的独立人工复核（第三批）。整理：金嘉睿（24151735）。2026-10-16。

## 一、填表规则完全沿用第二批说明书

第三批只是「用同一套流程又跑了一次」，判断「模型说的意思对不对」「作者写的标准答案本身对不对」的规则、八栏怎么填、以及红线的定义，与第二批**完全相同**。

请先打开 [第二批复核说明书](q001-batch2-review-guide.md)，按它的第三节到第七节填下面这两份表：

| 文件 | 行数 | 复核什么 |
| --- | --- | --- |
| `q001-batch3-review-worksheet.csv` | 90 | 模型每一次回答，答得对不对、有没有踩红线 |
| `q001-batch3-annotation-review-worksheet.csv` | 15 | 作者写的「标准答案」本身对不对 |

> `docs/evaluation/q001-live-batch3/` 里的 `answer-review.csv` 与 `annotation-review.csv` 是**证据文件，请不要改动**（这类文件在第三批是新加的，收在 `q001-live-batch3/` 子目录里，位置与第二批不同）。要填的永远是与本说明书同目录的两份工作表。

## 二、第三批与第二批的三处不同

| | 第二批 | 第三批 |
| --- | --- | --- |
| 配置组数 | 4 组（两个向量模型 × 两个回答模型，全部智谱） | **3 组**（全部用 embedding-3，分别配 GLM-4-Flash-250414、GLM-4.5-Flash、**DeepSeek-Flash**） |
| 行数 | 121 行 | 90 行（3 组 × 15 题 × 2 遍） |
| 进程中断残行 | 有 1 行（序号 37，模型未产生回答） | **没有**，本批 215 次请求全部成功、无中断、无重试 |

第三批**首次引入智谱以外的供应商（DeepSeek）参与回答对比**，这是验收条件①「供应商选择要有样本数据」的关键。所以这一批里「模型」那一栏会出现两家不同来源的模型名，请留意区分。

## 三、脚本已经查出来、你不用再查的

- `系统检出-坏引用编号`：本批 90 行**全部为空**，也就是没有错引。
- `系统检出-检索命中/应有`：三组都是 100% 命中（显示 `1/1` 或 `2/2`）；显示 `0/0` 的是刻意准备的「这门课没有任何资料」题目（美术课、音乐课），本来就该捞不到东西，属正常，不用写异常。

## 四、判断时的一个提醒

本批有几道题，**三组都在同样的地方出现同样的现象**（把「资料相关但缺关键信息」的 `uncertain` 答成了「完全没有资料」的 `insufficient`）。第二批的四组也是同样情况。

**同一现象出现在全部三组时，它更可能是提示词定义层面的共性问题，而不是模型之间的差异。** 这个提醒只是给你背景——对你填表没有影响：你仍然逐行按第二批第四节的例子判断，不需要替我们做归因，也不要在 `notes` 里猜「这个错是谁的锅」。

## 五、建议两批一次填完

第二批与第三批口径相同（同一份样本文件、同一提示词版本、同一 `sample_set_version`），可以合并成一轮做，减少重复熟悉成本：

| 批次 | 逐条回答复核 | 人工标注复核 |
| --- | --- | --- |
| 第二批 | `q001-batch2-review-worksheet.csv`（121 行） | `q001-batch2-annotation-review-worksheet.csv`（15 行） |
| 第三批 | `q001-batch3-review-worksheet.csv`（90 行） | `q001-batch3-annotation-review-worksheet.csv`（15 行） |

一次填不完没关系，填多少算多少，剩下的留空，并在 `notes` 里说明填到哪里。

## 六、填完之后

同第二批：告诉作者（或在 PR #378 下留言）你填好了、填了哪些行，由作者把数字回填进证据文件并汇总。**只有填了真名和日期这一步才算数**，作者不能替你签字。

## 七、这两份表是怎么来的

由脚本从证据文件生成，内容逐行对应，可以重新生成核对：

```powershell
uv run --locked --project backend python scripts/prepare_q001_review_sheet.py --experiment "v4-第三批=docs/evaluation/q001-live-batch3/answer-review.csv=三组各答 15 题 × 2 遍；首次跨供应商（智谱 + DeepSeek）" --annotation-source docs/evaluation/q001-live-batch3/annotation-review.csv --output docs/evaluation/q001-batch3-review-worksheet.csv --annotation-output docs/evaluation/q001-batch3-annotation-review-worksheet.csv
```

脚本**只读**证据文件，绝不改写它们；`系统检出-*` 两栏取自同目录的 `results.json`；脚本还会逐行检查两份文件是否对齐，对不上就直接报错。

第三批的运行过程、费用与结论见 [Q001 模型选型结论](../verification/Q001-model-selection.md) 与 [真实评测进度](../verification/Q001-live-progress.md)。
