# paper_keynote.md — 论文一句话读懂（v1，2026-09）

> 用于补齐论文节点的「一句话」：hover 与论文页首行显示。产出 `papers.keynote`（20–50 字中文）。

## 角色与任务

为输入的每篇论文写**一句让完全不了解该领域的人也能懂的话**：这篇论文做了什么、得出什么，
或为什么重要。**不是翻译标题**，是"一句话结论/定位"（如：用 fMRI 证明语音生成与理解在脑内走不同通路）。
若无法在输入信息（标题/年份/期刊/摘要片段）中确定结论，则写它"研究了什么问题/用什么方法"，不编造结果。

## 输入

- `data/pack_authors_*` 或 `_db` 论文清单（title/year/journal/摘要片段）
- 或图/代表论文清单（ID+标题+摘要片段）

## 输出（严格 JSON）

```json
{
  "domain": "neuroling",
  "prompt_ver": "paper_keynote.md@v1-202609",
  "model": "<agent 标识与日期>",
  "paper_keynotes": [
    {"paper_id": 45611, "keynote": "用 800 余人 precision fMRI 建出大脑语言网络的概率图谱（一句话，20-50 字）"}
  ]
}
```

## 硬约束

1. keynote 20–50 个中文字符；面向外行；避免术语堆砌（首次术语可括注白话）。
2. 结论须能由输入支撑；输入不足写研究问题/方法，不编造结果数字。
3. 撤稿论文在句末用括号注「（该文已撤稿）」。
4. 覆盖输入全部论文。

## 落库

写临时文件后执行（将写入 papers.keynote）：

```
python3 manage/keynote.py apply-papers <json>
python3 manage/keynote.py list-pending   # 或经 snapshot 审阅链
```
