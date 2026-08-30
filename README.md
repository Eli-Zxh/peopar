# 百官行述 · peopar

> 一个以**研究者**为中心的学术图谱系统：建立人员关系网络、按研究方向归纳研究者、标记与修订学术造假影响面。
> 名字取自清代人物传记集《百官行述》——为当代"百官"（研究者）立传、存真、辨伪。

## 为什么做这个

学科扩张太快，两个问题长期无解：

1. **脉络难建**——读论文看不见学界整体思路，热点更新跟不上；想知道"谁在做这个方向、怎么联系"没有系统性出口；
2. **纠错太弱**——学术造假出现后，被误导的方向难以被系统性标记与修正。

本系统以神经病学（阿尔茨海默病 + 神经语言学）为首个示例领域，但架构面向**任意学科**：一切收录规则由配置文件驱动。

## 定位（2026-08 调整）

- **前端形态**：**Obsidian 原生插件**（`obsidian-plugin/`）为第一入口——方向聚合图 / 方向·研究者 / 热点时间线 /
  研究者档案 / 造假事件 / 管理台，新拟态 + 柔和粉彩 UI，深浅自适应 Obsidian 主题；浏览器版 `web/index.html` 保留（双形态）。
- **三层架构**：L1 权威层（SQLite + CLI：采集/图谱/合成/写操作/审计）→ L2 投影层（`manage/export_vault.py`
  导出为 vault 内 `<方向名>/peopar/` 目录 md，frontmatter + wikilink）→ L3 展示层（插件**静态优先**读 vault 快照渲染，
  **零服务器零外部进程**；`enableServer` 开启后走本地服务提供实时数据与写操作；`.peopar` 文件触发视图）。
  文件格式契约见 `doc/obsidian-vault-format.md`。
- **数据与后端**：零依赖（Python 标准库 + SQLite + ECharts），本地服务 `python3 app.py` → http://127.0.0.1:8765（可选实时层）。
- **更新方式**：skill 式半自动——免费源（PubMed/OpenAlex）脚本一键增量；机构 webvpn（Scopus/CNKI/万方）**用户手动登录后，
  AI 助手检索导出、脚本导入**（无常驻进程、不自动调度）；更新后 `export_vault.py` 写 vault，Obsidian 文件监听自动刷新插件。
- **LLM 合成**：skill 式（AI 助手按版本化模板合成，人工在环审阅）——方向级簇快照、作者级画像、域级概览；
  产物一律锚定校验后以待审入库；无 API key、无本地模型依赖。
- **可视化侧重**：以**研究方向为第一视角**（方向聚合图 + 下钻作者层）、**方向→研究者**（谁在做这个方向）、
  **热点时间演化**（方向×年份热度）；L0/L1 造假标记保留并融入视图。

## 核心特性

- **研究者实体库**：系统自建主键（`BG######`），外部 ID（OpenAlex / ORCID / PubMed）仅作归并线索；全局别名表；双层制（核心层 + 外围层）。
- **共著关系图谱**：论文→作者→共著边，纯标准库标签传播社区发现；**方向聚合图**以方向簇为节点、簇间共享论文为边。
- **方向→研究者**：每个方向簇按实际研究思路命名，簇内列出核心研究者（机构官网校验 + 画像 + 代表作 + 联系线索）。
- **LLM 合成闭环**：方向快照 / 作者画像 / 域级概览，三级产物锚定校验 + 待审 + supersedes 链；LLM 判断（噪声簇/方向并拆/别名候选）→ 人工裁决队列。
- **造假标记闭环**：事件状态机、论文级标记、人员级分级（**L0 确认造假必须人工定性**；L1 共著风险提示）、受影响结论修订、全程审计留痕。
- **webvpn 半自动接入**：Scopus CSV / CNKI / 万方 RIS 导入，文件指纹去重，收录留痕可审计。
- **失效感知**：增量更新后论文集合指纹对比，过时快照自动标记（不自动重合成）。
- **簇只增不改**：历史方向不因增量更新被重算，保证可追溯。

## 快速开始

```bash
./update.sh             # 免费源增量更新（PubMed + OpenAlex + 图谱 + vault 投影导出）
python3 manage/export_vault.py "~/Documents/Obisidian Valut" --topic "神经语言学与失语症"   # 单独导出 vault 投影
./update_webvpn.sh neuroling data/webvpn/scopus_x.csv --source scopus --query "TITLE-ABS-KEY(...)"   # webvpn 导入
python3 app.py          # 可选：实时服务（插件 enableServer 时自动拉起，提供写操作/实时数据）
```

**Obsidian 插件**：`obsidian-plugin/` 构建后 symlink 进 `vault/.obsidian/plugins/peopar`（已在执行中完成）；
Obsidian 中启用「百官行述 · 研究者图谱」，ribbon 图标或命令面板「打开百官行述图谱」。

## 扩展任意新领域

收录规则全部在配置里，三步接入一个新学科（首次全量务必 `--full` 覆盖历史）：

```bash
# 1. 新建域（词表可用 AI 起草后人工核对）
python3 manage/domains.py new migraine --name "偏头痛" \
    --mesh "Migraine Disorders" "Migraine with Aura" \
    --keywords-strong "migraine"

# 2. 校验 MeSH 词（对照 NLM 官方词库）
python3 manage/domains.py check migraine

# 3. 全量采集（EDAT 年份窗口分片，覆盖全部历史）→ 回填 → 图谱
python3 ingest/pubmed.py migraine --full
python3 ingest/openalex.py migraine
python3 analyze/graph.py migraine
```

## 数据源

| 层 | 来源 | 通道 |
|---|---|---|
| A | PubMed (E-utilities)、OpenAlex | 免费直连（`--full` 分片全量 / `--incremental` 增量） |
| B | Scopus / CNKI / 万方 | 机构 webvpn（用户登录 → 导出 → `manage/webvpn.py import`） |
| C | Retraction Watch / PubPeer | 造假标注源 |

**论文收录判定为确定性规则**（MeSH 命中 ∪ 强关键词 ∪ 边界词共现 ∪ 引用种子扩散 ∪ webvpn 搬运），
可复现、可审计，**LLM 不参与收录判定**。

## 目录结构

```
app.py                  零依赖本地服务（http.server + SQLite + ECharts API）
schema.sql              实体模型（研究者/论文/簇/快照/造假事件/判断队列/webvpn 批次/审计）
config/domains/         各域收录规则（_template.json 为模板）
ingest/                 pubmed / openalex / webvpn_import（B 层题录解析）
analyze/                图谱与聚类 / 合成上下文导出（方向级 + 作者级 pack）
manage/                 domains、events、snapshot（含 apply-authors/staleness）、judgment、affiliations、webvpn
prompts/                合成模板：cluster_snapshot / author_profile / direction_researchers / domain_overview / webvpn_collect
web/ static/            浏览器版前端与 ECharts
obsidian-plugin/        Obsidian 原生插件（TypeScript + ECharts，新拟态粉彩 UI）
skill/SKILL.md          面向 AI 助手的操作手册（v2：webvpn/合成/裁决工作流）
data/                   SQLite 库、图谱 JSON、事件种子、合成上下文包
```

## 维护周期（建议）

- 免费源增量：每日（`./update.sh`）——可由系统 launchd/cron 触发，默认不配置（无常驻进程原则）。
- webvpn 源：每周（用户登录 → 导出 → `./update_webvpn.sh`）。
- 核心分析（图谱 + 合成 + 失效感知）：每月。

## 原则声明

数据来自公开学术 API 与机构授权访问源；**L0 造假定性必须人工确认**，系统不做有罪推定，共同作者不默认共谋；
LLM 合成产物默认待审、锚定校验；一切结论、履历、标记可下钻到来源与依据。

## License

MIT
