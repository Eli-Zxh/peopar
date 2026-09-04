---
name: peopar
description: 操作百官行述/peopar 研究者图谱系统（~/Documents/script/peopar）。当用户提到百官行述、peopar、研究者图谱、新增研究领域/学科域、文献增量更新、webvpn 采集（Scopus/CNKI/万方）、机构官网信息校验、造假标记、撤稿事件、汉字名校对、方向/作者画像合成时使用。涵盖建域、采集管线、webvpn 半自动采集、LLM 合成（方向/作者/域级）、造假闭环与常见陷阱。
version: 2.0.0
---

# 百官行述 · peopar 操作手册

以研究者为中心的学术图谱：共著网络 + 方向簇 + 研究者画像 + 造假标记闭环。
零依赖（Python 标准库 + SQLite + ECharts）。本地服务 `python3 app.py` → http://127.0.0.1:8765。

## 三层架构（2026-08 调整后）

| 层 | 载体 | 说明 |
|---|---|---|
| L1 权威层 | SQLite + CLI | 采集（PubMed/OpenAlex/webvpn）、图谱、LLM 合成、写操作、审计 |
| L2 投影层 | `manage/export_vault.py <vault> --topic "方向名"` | 核心数据 → vault 内 `<方向名>/peopar/` 目录 md（frontmatter + wikilink），幂等可重跑 |
| L3 展示层 | **Obsidian 插件**（obsidian-plugin/） | 静态优先：读 `<方向名>/peopar/` 渲染（离线零进程）；`enableServer` 开启后走本地服务（实时数据+写操作）；`.peopar` 文件触发视图 |

- **文件结构分层**：`<vault>/<方向名>/peopar/{directions,researchers,papers,events}/`——按研究大方向命名顶层目录（`--topic`），插件设置 `topic` 需与之一致。
- **文件格式契约**：见 `doc/obsidian-vault-format.md`（frontmatter type 区分 direction/researcher/paper/event；正文规范：方向=方案C折叠、研究者=三层研究方向、论文=方向角色锚定句；wikilink 走 Obsidian 原生）。
- 日常使用：纯插件读 vault 快照，**不依赖服务器、不依赖外部进程**。
- 数据更新：skill/agent 跑 CLI → 末尾执行 `export_vault.py --topic "…"` 写 vault → **Obsidian 文件监听自动刷新视图**（无需重启）。
- 写操作（审阅/裁决/校对/L0）：需开启插件「实时服务」（探测/拉起 python3 app.py）或用 CLI。
- 浏览器版 web/index.html 保留（兼容）。

## 新方向的仓库决策规则

先统计目标方向作者与现有域作者的重叠比例：
- **作者重叠 ≥20%**（同一批人）→ 补进原库：`python3 manage/domains.py new`（默认；BG 身份/画像/造假标记一次维护）
- **基本零重叠 + 数据源独立**（跨学科新领域）→ 新建独立 peopar 仓库（避免库膨胀与跨域噪声）

## 不可违反的原则

1. 研究者主键为系统自建 `BG######`；外部 ID（OpenAlex/ORCID/PubMed）只是归并线索，入别名表。
2. 论文收录判定 = 确定性规则（MeSH ∪ 强关键词 ∪ 边界词共现 ∪ 引用扩散 ∪ webvpn 搬运），**AI 绝不参与收录判定**。
3. **L0（确认造假）必须用户人工定性**；自动标记止步于 L1 风险提示；共同作者≠共谋。AI 不得代为确认任何 L0 或事件状态。
4. 方向簇只增不改：禁止手工 UPDATE clusters/历史 batch；`clusters.display='excluded'` 仅是展示层折叠。
5. 一切写入留审计（audit_log）；代用户执行写操作时 actor 用 `user:<署名>` 并先向用户确认。
6. LLM 产物（方向/作者/域级）一律锚定校验后以「待审」入库；重合成走 supersedes 链；全程审计。

## 命令速查

```
启动应用          python3 app.py                       # 127.0.0.1:8765（仅实时模式需要）
免费源增量        ./update.sh                          # PubMed 增量 + OpenAlex + 图谱 + vault 投影导出
vault 投影导出    python3 manage/export_vault.py "<vault路径>" [--domain a,b] [--researcher-min-papers 3]
                   # 导出后 Obsidian 插件（.peopar 视图）经文件监听自动刷新
PubMed 全量修复   python3 ingest/pubmed.py <域> --full # EDAT 年份窗口分片，覆盖全部历史
webvpn 导入       python3 manage/webvpn.py import <文件> --source scopus|cnki|wanfang --domain <域> [--query ...]
webvpn 批次       python3 manage/webvpn.py list [--domain <域>]
域管理            python3 manage/domains.py new|list|check|remove
图谱刷新          python3 analyze/graph.py <域id>
合成上下文        python3 analyze/extract.py <域id>                      # 方向级 pack
                  python3 analyze/extract.py <域id> --authors [--scope core-flagged|--ids ...]   # 作者级 pack
方向快照入库      python3 manage/snapshot.py apply <json> [--by 署名]
作者画像入库      python3 manage/snapshot.py apply-authors <json> [--by 署名]
失效感知          python3 manage/snapshot.py staleness <域id>
快照清单/审阅     python3 manage/snapshot.py list [域id] | review <id> approve|reject --by 署名
LLM 提案裁决      python3 manage/judgment.py propose <json> | list [--status X] | decide <id> accept|reject --by 署名 [--note ...]
机构官网信息      python3 manage/affiliations.py add <BG…> --institution "机构" [--role 职位] [--url ...] [--email ...] --by 署名
                  python3 manage/affiliations.py verify|dismiss <id> --by 署名 | queue | list <BG…>
造假事件          python3 manage/events.py apply <json> | list | confirm-l0 | confirm-event
布局评分入库      python3 manage/layout_scores.py apply <json> | list [--pending] | review <id> approve|reject --by 署名
图谱布局求解      python3 analyze/layout.py <域> [--k 12] [--include-pending] [--seed 42]
人工修订同步      python3 manage/notes_sync.py scan|apply <vault> [--topic 方向名] [--by 署名] [--sync]
标签管理          python3 manage/tags.py seed <json> | list-vocab [--dim X] | propose-vocab <json> --by 署名
                  python3 manage/tags.py review-vocab <id> approve|reject --by 署名
                  python3 manage/tags.py suggest <json> | list-tags <BG…>

**V3 布局评分任务（模型见 `doc/visualization-model.md`）**：
- `direction_map`：方向两两关联度 s(i,i′)(模板 `prompts/direction_map.md`，K(K−1)/2 对)
- `direction_affinity`：每方向 top 论文 × 方向关联度 a(j,i)（模板 `prompts/direction_affinity.md`）
- 评分 → `layout_scores.py apply`（待审）→ 人工 `review` 批准 → `layout.py` 求解坐标
  → export_vault 导出 `_layout/<domain>.json` → 插件信息化方向图谱（区域+论文/作者散点+连线）
```

## 工作流 1：新增研究领域（AI 核心参与点）

1. 起草词表：`mesh_terms`（精确 MeSH 词）、`mesh_boundary`（过宽词）、`keywords_strong`（tiab 短语，支持 * 通配）、`cooccur_terms`（语境约束词）、`author_queries`（锚定人物范例域）。种子论文放 `seeds.seed_dois`。
2. `python3 manage/domains.py new <id> --name ... --mesh ... --keywords-strong ...`
3. `python3 manage/domains.py check <id>` —— MeSH 词必须有效。
4. 小量试跑：`python3 ingest/pubmed.py <id> --limit 100`，抽查标题是否合域。
5. **首次全量必须用 `--full`**（EDAT 年份窗口分片），否则 `sort:pub_date` 会把历史论文截断丢失（已踩坑）。
6. `ingest/openalex.py <id>` → `analyze/graph.py <id>`，浏览器/插件看图验证聚类质量。

## 工作流 2：增量更新与失效感知

- `./update.sh`（PubMed 按 EDAT 窗口增量 + OpenAlex 回填 + 图谱刷新）。建议周期：免费源每日、订阅/中文源每周（webvpn 半自动）、核心分析每月。
- 增量后执行 `python3 manage/snapshot.py staleness <域>`：论文集合变化的快照置 `affected_pending_review`，**不自动重合成**——重合成是 LLM 工作，由 skill 会话决定。

## 工作流 3：webvpn 半自动采集（Scopus / CNKI / 万方）

详见 `prompts/webvpn_collect.md`。要点：
1. **用户手动登录机构 webvpn 门户并保持会话**（验证码/2FA 无法脚本化；本项目无常驻进程，不自动调度）。
2. AI 助手在门户内检索 → 导出标准题录：Scopus CSV（勾选作者/标题/年份/DOI/期刊/机构/被引/摘要）、CNKI/万方 NoteExpress 或 EndNote（RIS）。
3. `python3 manage/webvpn.py import <文件> --source scopus|cnki|wanfang --domain <域> --query "<检索式>"`。
4. 文件指纹去重；收录留痕 `keyword` + `matched_term=webvpn:<source>:<检索式>`。
5. 导入后：图谱刷新 + staleness；中文作者进「待校对队列」。

## 工作流 4：登记造假事件

1. 收集来源 URL（Retraction Watch / PubPeer / 新闻调查）。
2. 仿 `data/lesne_event.json` 起草 `data/<slug>_event.json`（l0_candidates.name_query 须与库内 name_display 一致）。
3. `python3 manage/events.py apply data/<slug>_event.json` —— 自动挂接 PubMed 权威撤稿标记 + L1 共著提示。
4. **L0 确认与事件确认留给用户在网页/插件上做**；AI 只整理材料。

## 工作流 5：机构官网信息抓取校验（「可联系研究者」必要数据）

1. 从「方向·研究者」清单/核心层选出目标研究者（核心层 + 被标记 + 用户指定）。
2. AI 助手浏览器/curl 访问其任职机构主页、实验室页、ORCID，提取：机构、职位、邮箱、研究方向简介。
3. 逐条 `python3 manage/affiliations.py add <BG…> --institution "…" --role "…" --url "…" --email "…" --by <署名>`（source_tag='web'，verified=0）。
4. 用户在插件管理台「机构官网信息校验」队列确认；确认后才在档案/方向·研究者视图标 ✅。
5. 邮箱只做联系线索展示，不公开导出；遵守机构网页使用条款。

## 工作流 6：方向级 / 作者级 / 域级 LLM 合成（skill 式）

1. 方向级：`python3 analyze/extract.py <域>` 导出 pack → 按 `prompts/cluster_snapshot.md` 合成（方向名/概述/当前结论/历史/分歧，paper_id 锚定）→ `manage/snapshot.py apply`。
2. 作者级：`python3 analyze/extract.py <域> --authors`（每包 ≤40 人）→ 按 `prompts/author_profile.md` 合成画像 → `manage/snapshot.py apply-authors`。
3. 方向→研究者深度清单：按 `prompts/direction_researchers.md` 合成（role/reason/contact_note）→ `manage/judgment.py propose`（作为 LLM 增强层；基础清单由 API 确定性生成）。
4. 域级概览：`prompts/domain_overview.md` → `manage/snapshot.py note <域> <md>`（PMID/DOI 必须存在于域内库）。
5. 全部产物 pending 待审；发现噪声簇/方向并拆/别名候选 → `manage/judgment.py propose`（jtype 枚举：noise_cluster/direction_merge/direction_split/alias_candidate）。

## 工作流 7：校对与裁决（人工在环）

- 插件管理台集中：方向/作者快照审阅、LLM 建议裁决（采纳噪声簇 → 侧栏折叠）、别名校对、机构信息校验、webvpn 批次、审计日志。
- CLI 等价：`snapshot.py review` / `judgment.py decide` / `affiliations.py verify`。

## 工作流 8：研究者深描（平级独立 skill，手动触发）

为**单个重要研究者**做全面画像 + 标签补全（用户点名触发；不做批量）。模板 `prompts/researcher_deepdive.md`。
1. agent 采集机构公开信息（官网/课题组/ORCID，从简）→ 结合本地论文/方向记录
2. 产出 career/profile/tag_suggestions/note/sources（全部锚定 URL 或论文）
3. `manage/tags.py propose-vocab`（词表外新词）→ `suggest`（研究者标签建议 pending）
4. `manage/affiliations.py add`（机构履历，待校验）→ 用户在管理台批准
5. 画像/批注经 `notes_sync` 或研究者 vault 笔记批注区保留

## 工作流 9：人工编辑同步（vault → SQLite）

- 人工修订区：frontmatter `manual_*` 键 + 正文 `<!-- PP-MANUAL-START -->…<!-- PP-MANUAL-END -->`
  ——export_vault **永不覆盖**；文档结构见 `doc/obsidian-vault-format.md`
- `notes_sync.py scan`（只读列出）→ `apply --by 署名`（白名单字段回写；`--sync` 清标记）
- 白名单：研究者 `manual_name_zh`→authors.name_zh、`manual_note`→authors.note；论文 `manual_paper_note`→papers.note；
  L0/事件/簇成员/收录等**不可**经 md 修改；结构化变更走 judgments/裁决队列

## 陷阱（实测踩过）

- **PubMed `--full` 必须用于首次全量**：默认 `sort:pub_date` + max_fetch 会把历史论文截断（22.9 万命中只取最新 1 万）。EDAT 窗口分片（1900–今）修复；极热区间窗口命中 >9000 会继续二分。
- E-utilities 无 API key 限 3 req/s，代码内置 sleep；网络断连/响应不完整（IncompleteRead）已加入重试，勿删。
- PubMed esearch 返回 JSON 可能含控制字符：fetch 已用 `strict=False`。
- OpenAlex：`select` 无 `pmid` 字段用 `ids`；`per-page` 上限 200 用 `cursor`；filter 多值用 `|`。
- 单篇 >25 作者不生成全对共著边；单篇外围作者默认不上图（在库中保留）。
- 方向聚合图只取规模 top 40（≥3 人）主要方向——最新批次可能数千小簇，全量渲染会卡。
- 中文作者（webvpn 导入）无拼音键：按 name_zh/name_display + 机构短键归并，新建进待校对。
- `webvpn_imports.source` 枚举为 scopus/cnki/wanfang；文件格式由 `--source` 推断（scopus→CSV，其余→RIS）。
- 库文件在 `data/peopar.db`（WAL 模式）；备份 = 复制该文件。

## 验证

`sqlite3 data/peopar.db "SELECT COUNT(*) FROM papers"` 等；插件/浏览器逐页查看；审计：管理台「操作留痕」。
