# 架构补充设计：LLM 合成层 v0.2（草案，待批准）

> 本文档补齐新架构中缺失的部分：**每个作者的 LLM 总结**、**每个研究方向的深度分析**、以及**需要 LLM 判断并修正的环节**。批准后按第 5 节批次实施；第 6 节为待决策问题。

## 0. 本设计要解决的问题

1. **作者级分析缺失**：系统目前对任何研究者都没有画像式总结。
2. **方向级分析未落地**：簇以编号命名，研究概述/当前结论/历史进程只有模板没有产物。
3. **可视化未体现领域方向结构**：侧栏与图谱应以「方向」为第一视角。
4. **缺少 LLM 判断修正环节**：噪声簇（同名误并、离题簇）、方向边界、别名归属等问题，纯确定性管线无处安放，需要「LLM 判断 → 人工裁决」的机制。

## 1. 红线（继承原始设计，不放宽）

1. LLM 绝不参与收录判定——论文/作者入库只由确定性规则决定。
2. 造假定性（L0）仅人工；LLM 在作者合成中**不得超出现有人工标记**做造假定性，无标记时风险字段只能写「无风险标记」。
3. `clusters` 成员只增不改；LLM 判断**不移动簇成员**，只作用于展示层/方向视图/快照措辞。
4. 所有 LLM 产物：锚定校验 → 默认待审 → 人工审阅；记录 `model`/`prompt_ver` 溯源；全程写 `audit_log`。

## 2. 三级 LLM 合成体系

### 2.1 方向级：簇快照（已有基建，缺产物）

- 模板：`prompts/cluster_snapshot.md`（当前 v2）：方向名 + 研究概述 + **当前结论** + 历史进程 + 分歧与风险 + 代表/支撑文献锚定（paper_id 必须属于该簇）。
- 待产出：neuroling 10 个主簇 + ad_lesne 主簇（簇 1 用 v2 重合成，走 supersedes 链）。
- 合成中发现的噪声簇/边界问题不在快照里硬写，转第 3 节判断队列。

### 2.2 作者级：研究者快照（新增，本设计的核心补充）

**模板** `prompts/author_profile.md`（新增，v1 草案）：

输入（确定性导出，禁止 LLM 自行检索）：
- 该作者全部论文：标题/年份/期刊/被引/撤稿状态/收录规则（全量，不截断——外围作者论文少，核心作者截断会丢信息，超长时按被引降序截取并在材料中标注「已截取」）；
- 所属簇及簇的方向名（若已有快照）；
- 前 10 合作者及共著篇数；
- 现有 L0/L1 标记、事件名与依据原文；
- 已知别名、履历（若有）。

输出（严格 JSON）：

```json
{
  "author_id": "BG000123",
  "focus": "≤30字的研究焦点",
  "summary": "3-5句画像：研究什么问题、典型方法、代表性成果、在方向中的位置。每个论断锚定 paper_id。",
  "key_contributions": "2-4条主要贡献，每条一句并锚定 paper_id",
  "risks": "客观转述：无标记→“无风险标记”；有标记→逐字转述标记级别、状态与依据，禁止进一步定性或推测",
  "representative_paper_ids": [1, 2, 3]
}
```

**范围**（待决策，见 Q7）：建议 = 核心层（226 人）+ 全部被标记作者 + 用户指定名单；其余外围作者按需触发（档案页「生成总结」按钮，后续可加）。

**存储**：新表 `author_snapshots`，治理结构与 `snapshots` 完全对齐：

```sql
CREATE TABLE IF NOT EXISTS author_snapshots (
    id INTEGER PRIMARY KEY,
    author_id TEXT NOT NULL REFERENCES authors(id),
    content TEXT NOT NULL,           -- 上述 JSON
    model TEXT, prompt_ver TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded')),
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
    reviewed_by TEXT, reviewed_at TEXT,
    supersedes INTEGER REFERENCES author_snapshots(id),
    generated_at TEXT NOT NULL DEFAULT (datetime('now')));
```

**上下文导出**：`analyze/extract.py` 增加 `extract_authors(domain, scope)` → `data/pack_authors_<domain>_<批次>.json`（每包约 40 人，控制合成上下文规模）。

**入库**：`manage/snapshot.py` 增加 `apply-authors <json>`（锚定校验：representative_paper_ids 必须属于该作者的论文；author_id 必须属于该域）。

**UI**：研究者档案页顶部新增「合成简介」卡片（focus/summary/贡献/风险 + 审阅徽标），管理台审阅队列合并显示簇快照与作者快照。

### 2.3 域级：概览笔记（已有）

`prompts/domain_overview.md` v1 + `manage/snapshot.py note`（引用必须存在于域内库）。放在方向级、作者级产物完成后写，使概览能引用已命名的方向。

## 3. LLM 判断修正机制（新增）

统一表 `judgments` + 管理台「LLM 建议队列」。全部 **LLM 提案 → 人工裁决 → 留痕**；接受/驳回都不修改簇成员历史。

| 判断点 | 触发场景 | 提案内容 | 人工接受后的动作 |
|---|---|---|---|
| 噪声簇 | 方向级合成发现簇主题与域定位不符（如 ad_lesne 中的腈水合酶簇、血红蛋白簇，系作者同名误并） | `jtype=noise_cluster` + 簇内论文锚定理由 | 侧栏降入「已排除方向视图」区；簇成员与数据不动 |
| 方向合并/拆分 | 合成发现两簇实为同一方向，或一簇含两个子方向 | `jtype=direction_merge/split` + 两侧证据 | 仅影响快照措辞与显示分组（见 Q8） |
| 别名/同一人 | 合成中发现汉字名候选或同一人多签名线索 | `jtype=alias_candidate` | 进入现有别名校对队列（verified=0），人工核验 |
| 快照修订 | 快照被驳回 | 按驳回意见重合成，supersedes 旧快照 | 重新走审阅 |

```sql
CREATE TABLE IF NOT EXISTS judgments (
    id INTEGER PRIMARY KEY,
    jtype TEXT NOT NULL,             -- noise_cluster|direction_merge|direction_split|alias_candidate
    entity_type TEXT, entity_id TEXT,-- 对应 cluster/author
    proposal TEXT NOT NULL,          -- JSON：建议内容 + 理由 + 锚定 paper_id
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
    decided_by TEXT, decided_at TEXT, decision_note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
```

噪声簇被接受后，`renderClusters` 将其归入折叠区；域级概览笔记中如实说明「本域存在 N 个同名误并簇，已排除」。

## 4. 数据流总览

```
采集（确定性，无 LLM，不变）
  → extract（确定性导出簇/作者上下文包）
  → agent 按版本化模板合成（方向级/作者级/域级 + judgments 提案）
  → apply 锚定校验入库（默认 pending）
  → 人工审阅 / 裁决（网页或 CLI，均留痕）
  → 可视化呈现（方向命名、快照面板、作者档案、建议队列）
  → audit_log 全链路 + model/prompt_ver 溯源
```

## 5. 分批实施计划（批准后执行）

| 批次 | 内容 | 交付物 |
|---|---|---|
| A | 方向级：neuroling 10 簇 + ad_lesne 簇 1 重合成；过程中产出噪声簇 judgments（ad_lesne 预计 ~10 个非本域簇提案） | 方向命名可视化可用 |
| B | 作者级：`author_snapshots` 表 + `extract_authors` + `author_profile.md` + `apply-authors`；合成核心层 ~226 人 + 被标记作者（约 8 包）；档案页接入 | 作者合成简介上线 |
| C | 域级：两域概览笔记；管理台 judgments 队列 UI | 领域整体视图 |
| D | tests/ 回归、命名规范化（skill→researcher-atlas、去除本地代号）、v0.1.0 tag | 可分享版本 |

## 6. 待决策问题

- **Q6**：今日已实施的 4 处方向中心化改动（见 `doc/work-log-20260826.md` 第三节）如何处理？
  A) 保留（推荐）——只动了模板、展示层与一个展示名字段，无数据库内容写入，且与你指出的方向一致；
  B) 定点回滚，等本设计批准后重新实施。
- **Q7**：作者级快照范围？
  A) 核心层 + 全部被标记作者 + 用户指定名单（推荐）；
  B) 全部作者（3.2 万人，成本高、外围单篇作者信号弱）；
  C) 仅核心层。
- **Q8**：方向合并/拆分建议的落地形态？
  A) 只在显示层/快照措辞体现（推荐，轻量、不触簇历史）；
  B) 建独立「方向视图表」承载方向的合并与层级（更结构化，复杂度高）。
- **Q9**：批次顺序？
  A) A→B→C→D（推荐，先让方向可视化可用）；
  B) B 优先（作者档案优先）。
