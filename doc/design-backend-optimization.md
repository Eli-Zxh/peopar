# 后端优化设计 v0.2（草案，待批准）

> 配套文档：`doc/design-llm-synthesis-v0.2.md`（三级合成体系与红线）。本文档专讲后端：数据模型、管线机制、API、失效治理。**前端可视化优化不在此范围，后续单独讨论。**

## 0. 现状盘点与后端短板

已具备：采集（PubMed+OpenAlex，确定性）、共著图谱与聚类（只增批次）、造假闭环、快照基建（extract→合成→锚定校验→待审→审阅）、审计留痕。

后端短板：

1. 无作者级合成的存储与导出（缺表、缺上下文包、缺入库命令）。
2. 无「LLM 判断 → 人工裁决」的承载（噪声簇、方向并拆、别名候选无处安放）。
3. 合成产物无失效感知：增量更新后，旧快照引用的论文集合可能已变化，系统不会发现。
4. 合成上下文包只有簇级，无作者级；无截断标注策略。
5. API 缺作者快照、判断队列、统一审阅队列（分类型）端点。

## 1. 数据模型增量（schema.sql + ingest/common.py 迁移）

### 1.1 author_snapshots（作者级快照）

```sql
CREATE TABLE IF NOT EXISTS author_snapshots (
    id INTEGER PRIMARY KEY,
    author_id TEXT NOT NULL REFERENCES authors(id),
    content TEXT NOT NULL,            -- {focus, summary, key_contributions, risks, representative_paper_ids}
    model TEXT, prompt_ver TEXT,
    basis_signature TEXT,             -- 合成时该作者论文集合的指纹（失效感知用，见 §3）
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','affected_pending_review','superseded')),
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
    reviewed_by TEXT, reviewed_at TEXT,
    supersedes INTEGER REFERENCES author_snapshots(id),
    generated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS ix_author_snapshots_author ON author_snapshots(author_id);
```

### 1.2 snapshots 增补列

- `basis_signature TEXT`：合成时簇论文集合指纹（旧行迁移为 NULL，视为「无指纹，不参与失效判定」）。

### 1.3 judgments（LLM 判断裁决队列）

```sql
CREATE TABLE IF NOT EXISTS judgments (
    id INTEGER PRIMARY KEY,
    jtype TEXT NOT NULL CHECK (jtype IN ('noise_cluster','direction_merge','direction_split','alias_candidate')),
    entity_type TEXT NOT NULL,        -- cluster | author
    entity_id TEXT NOT NULL,
    proposal TEXT NOT NULL,           -- JSON：建议内容 + 理由 + 锚定 paper_id
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','accepted','rejected','superseded')),
    decided_by TEXT, decided_at TEXT, decision_note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
```

### 1.4 clusters 增补列

- `display TEXT NOT NULL DEFAULT 'normal' CHECK (display IN ('normal','excluded'))`：噪声簇裁决接受后置 `excluded`（**仅展示属性**，簇成员历史不动，符合只增不改原则）。

### 1.5 原则声明

以上所有新增均为「只加不改」：不修改既有表既有列语义；旧库经 `init_db` 的 ALTER 迁移循环自动升级（沿用现有迁移模式）。

## 2. 上下文导出（analyze/extract.py 扩展）

### 2.1 作者级上下文包

新增 `extract_authors(domain, scope) -> data/pack_authors_<domain>_<n>.json`：

- scope 三种形式：`--scope core-flagged`（默认：核心层 ∪ 被标记作者）、`--ids BG000123,BG000456`、`--from-file list.txt`。
- 每人导出：全部论文（标题/年份/期刊/被引/撤稿/收录规则，**不截断**；单作者 >80 篇时按被引降序截取并在包内标注 `truncated: true`）、所属簇 + 簇方向名（若已有快照）、前 10 合作者（姓名+共著篇数）、L0/L1 标记及事件名与依据原文、别名、履历。
- 每包 ≤40 人，控制单次合成上下文规模。

### 2.2 簇级包增补

- 每个簇对象增加 `basis_signature`（论文 id 集合的稳定哈希：排序后拼接取 sha256 前 16 位），供 apply 时落入快照行。

## 3. 失效感知机制（增量更新后的合成产物治理）

机制：**指纹对比**。

1. 合成入库时记录 `basis_signature`（论文集合指纹）。
2. 新增命令 `python3 manage/snapshot.py staleness <domain>`：
   - 对每条 `status='active'` 的快照重算当前指纹；不一致 → `status='affected_pending_review'`，写审计；
   - 无指纹的旧快照跳过（输出提示）。
3. `update.sh` 在 ingest/openalex/graph 之后对每个域追加一行 `staleness` 调用；**不自动重合成**——重合成是 LLM 工作，由用户/定时触发的 agent 会话决定。
4. UI 表现（在现有框架内，不涉及新前端设计）：侧栏快照徽标增加「已过时」态；审阅队列列出受影响条目。

## 4. 入库与裁决命令（manage/）

### 4.1 snapshot.py 扩展

- `apply`：落 `basis_signature`；行为不变（锚定校验、supersedes、写 clusters.name）。
- `apply-authors <json>`：校验 author ∈ 域、representative_paper_ids ⊆ 该作者论文；落 author_snapshots（pending）。
- `staleness <domain>`：见 §3。
- `list`：同时列出两类快照（带类型列）。

### 4.2 manage/judgment.py（新增）

- `propose <json>`：agent 提案入库（批量；同一 jtype+entity 已有 pending 提案时拒收，防重复）。
- `list [--status X]`。
- `decide <id> accept|reject --by <署名> [--note ...]`：
  - `noise_cluster` accept → `clusters.display='excluded'`；
  - `alias_candidate` accept → 插入 `author_aliases`（verified=0，source='llm'），进入现有校对队列；
  - `direction_merge/split` accept → 仅留痕并作为下一轮合成的措辞依据（展示分组待前端讨论，后端不抢跑）；
  - 同一 jtype+entity 的旧 accepted 判决被新判决 `superseded`（判决可被后续判决覆盖，但历史不删）。
- 全部动作写 `audit_log`。

## 5. API 增量（app.py）

| 端点 | 说明 |
|---|---|
| `GET /api/author/<id>/snapshot` | 该作者最新快照（content + 代表论文解析） |
| `GET /api/judgments?status=pending` | 判断队列 |
| `POST /api/judgment/<id>/decide` | `{action, by, note}`，与 CLI 等价 |
| `GET /api/snapshot-queue` | 扩展：合并簇快照/作者快照/受影响快照，带 `kind` 字段 |
| `GET /api/graph` | 簇对象追加 `display`（供侧栏折叠噪声簇）——沿用现有合并逻辑 |

作者档案页接入 `author-snapshot` 卡片、管理台接入判断队列：属于现有页面内的增量渲染，不引入新页面（大改前端留待专项讨论）。

## 6. 红线与治理（再确认）

1. 收录判定无 LLM；LLM 产物一律锚定校验 + 默认待审。
2. L0 定性仅人工；作者快照的 risks 字段机械校验做不到「不越界定性」，靠模板红线 + 人工审阅双保险。
3. judgments 的任何 accept 都不修改簇成员、不删数据。
4. 失效标记是确定性机制，不依赖 LLM。

## 7. 实施顺序（后端优先，前端另议）

1. **S1 数据模型**：schema + 迁移（§1）。
2. **S2 导出与入库**：extract_authors、apply-authors、apply 落指纹（§2、§4.1）。
3. **S3 judgments**：judgment.py + API（§4.2、§5）。
4. **S4 失效感知**：staleness + update.sh 接入（§3）。
5. **S5 内容生产（方向级）**：neuroling 10 簇 + ad_lesne 簇 1 重合成 + 噪声簇提案。
6. **S6 内容生产（作者级）**：核心层+被标记作者合成（约 230 人 / 6 包）。
7. **S7 域级概览 + tests/ 回归 + v0.1.0**。

S1–S4 为纯机制建设，完成即可用；S5–S6 是内容生产，产物全部进待审队列等你审阅。

## 8. 明确不做（本版本）

- 前端大改（方向视图层级、视觉重构）——单独立项讨论。
- recheck 式论文级数据清洗——已叫停。
- FTS5 全文检索、作者按需生成按钮——可选增强，非本期。
