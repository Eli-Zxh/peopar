# direction_map.md — 方向两两关联度评分模板（v1，2026-08-31）

## 角色与任务

你是文献计量分析助手。输入是某一研究领域内的**主要研究方向清单**（每个方向含名称与方向快照概述）。
请评估**方向两两之间的主题关联度** `s(i,i′) ∈ [0,1]`：两方向在研究对象/问题/方法上的实际重合与协同程度。
该评分将决定可视化宏观图中方向中心的相对位置（关联越强中心越近，可重叠示意包含关系）。

**注意**：这不是"相似度等于同义"——请区分：
- **相关/协同**（共享问题或方法，常互相引用、跨方向合作）→ 高值
- **包含/覆盖**（一个方向是另一个的子集或与之有重叠）→ 高值（并可在 reason 注明包含方向）
- **无实质关联**（只是同域但做不同问题、不同人群）→ 低值

## 输入

`data/pack_<domain>.json`（各簇含 name/definition/current_conclusions/timeline）——以已命名的
方向快照为评分依据；未命名方向不参与本轮。

## 输出（严格 JSON，无其他文字）

```json
{
  "domain": "neuroling",
  "prompt_ver": "direction_map.md@v1-20260831",
  "model": "<agent 标识与日期>",
  "dir_sim": [
    {"from_cluster": 9351, "to_cluster": 7395, "value": 0.8,
     "reason": "共享卒中后失语人群与康复结局研究（举例锚定方向快照中的主题），且有重叠团队"},
    {"from_cluster": 9351, "to_cluster": 9356, "value": 0.3,
     "reason": "仅共享语言神经科学方法论，研究对象（神经退行 vs 语言模型）不同"}
  ]
}
```

## 硬约束

1. 覆盖**全部方向对**（K(K−1)/2 对，无遗漏）；value 为 [0,1] 一位小数。
2. `reason` 1 句，须基于方向快照中的实际内容；不确定写"快照显示无明显关联"。
3. 包含/覆盖关系在 reason 中显式说明（"方向 X 大体包含于 Y 的 …"）。
4. 输出必须是可 `json.loads` 的单个 JSON 对象；from/to 为方向 cluster id。

## 落库方式（由 agent 执行）

写入临时文件后执行：

```
python3 manage/layout_scores.py apply <文件>
python3 manage/layout_scores.py list --pending
```

人工批准后（`review <id> approve|reject --by <署名>`）评分方可参与布局（`analyze/layout.py`）。
