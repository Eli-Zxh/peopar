# Obsidian 插件文件规范（v1，2026-08-30）

> 本文件是 L2 投影层与 L3 展示层的**契约**：`manage/export_vault.py` 按此生成，
> Obsidian 插件（obsidian-plugin/）按此读取。改格式必须同步改两端。

## 一、三层架构

| 层 | 载体 | 说明 |
|---|---|---|
| L1 权威层 | SQLite + CLI | 采集（PubMed/OpenAlex/webvpn）、图谱、LLM 合成、写操作、审计 |
| L2 投影层 | `manage/export_vault.py <vault> --topic "方向名"` | 核心数据 → vault 内 `<方向名>/peopar/` md（幂等可重跑） |
| L3 展示层 | Obsidian 插件 | 静态优先读 vault md（零服务器）；`enableServer` 走本地服务（实时+写操作） |

- 日常使用：纯插件读 `<方向名>/peopar/` 快照，不依赖服务器、不依赖外部进程。
- 数据更新：skill/agent 跑 CLI → `export_vault.py` 写 vault → **Obsidian 文件监听自动刷新视图**。
- 写操作（审阅/裁决/校对/L0）：需开启插件「实时服务」或用 CLI。

## 二、文件结构（按研究大方向分层）

```
<vault>/
└── <方向名>/                        # --topic（如 神经语言学与失语症）
    └── peopar/                      # 插件唯一读取目录（其他目录不受影响）
        ├── _sync.md                 同步状态（synced_at / topic / domains）
        ├── <domain>.peopar          域视图入口（双击以插件视图打开）
        ├── directions/direction-<cid>.md   研究方向笔记
        ├── researchers/BG<id>.md    研究者笔记
        └── events/event-<id>.md     造假事件笔记
        （论文**不进 vault**：DB 权威存储，图谱/布局 JSON 携带标题/摘要/笔记；见下）
```

命名约定：**文件名用系统主键**（direction-<cid> / BG<id> / paper-<id> / event-<id>），
与 SQLite 一一对应，重导出不换名 → wikilink 永不失效。

## 三、frontmatter 契约（插件数据入口 = `<方向名>/peopar/` 前缀 + `type` 字段）

| type | 文件 | 关键 frontmatter | 插件用途 |
|---|---|---|---|
| `direction` | directions/… | `domain, direction_id, name, size, papers, recent, citations, review, label, years(JSON字符串), linked[], top_authors[]` | 方向聚合图、热点时间线 |
| `researcher` | researchers/… | `id, name, name_zh, tier, domain, papers, institution, inst_verified, orcid, flags[], directions[], representative[], focus, review` | 方向·研究者、档案、搜索 |
| （论文） | 不建文件 | 存 DB（title/abstract/note/abstract_override…）；布局 `_layout/<domain>.json` 携带 title/abstract 摘要节选/note/pmid | 图谱节点 hover/面板、编辑 |
| `event` | events/… | `event_id, slug, title, status, source_urls, paper_flags[], author_flags[]` | 造假事件页 |
| `peopar-view` | <domain>.peopar | `domain, type: peopar-view, topic` | 扩展名文件视图触发 |
| （元数据） | `_sync.md` | `synced_at, topic, domains` | 视图显示同步时间/新鲜度 |

序列化约定：字符串加引号；复杂结构（years/linked）存为**带引号 JSON 字符串**（YAML 安全），插件 `JSON.parse`；
列表内字符串元素加引号（防名字含逗号破坏 YAML）。

## 四、正文规范（frontmatter 是数据，正文是人读的）

### 方向笔记（方案 C：callout + 折叠叙述 + 链接清单）
```markdown
# <方向名>

> [!summary] 方向概览
> 规模 **N** 人 · 论文 **N** · 近三年 **N** · 被引 **N** · 审阅：pending

<details>
<summary>📖 方向叙述（LLM 合成，待审）</summary>

## 研究概述 / 当前结论 / 历史进程 / 分歧与风险   ← LLM 快照四节，默认折叠
</details>

## 代表研究者
[[BG000117|David J. Irwin]]、…

## 代表论文
[[paper-35891|标题]]、…
```

### 研究者笔记（研究方向三层：确定性优先，LLM 仅已审展示）
```markdown
# <name>（中文名）

> [!info] 概要
> 🎯 focus（仅 review=approved 展示；pending 标"画像待审"） · 🏛 机构 · 核心层 · 域内论文 N

（summary 段落，仅已审）

**主要贡献**：…（仅已审）

**研究方向**
- 所属方向：[[direction-8769|<方向名>]]       ← 确定性（聚类）+ 人工审阅的方向名
- 代表作：[[paper-43154|<标题>]]、…           ← 确定性（论文事实）
（LLM focus/summary 作为增强层，待审不冒充结论）

⚠️ 标记：L1（如有）
```

### 论文（DB 节点数据，非文件）
论文内容：题目/期刊/年份/摘要/笔记/原文链接/作者——存 SQLite 权威层；图谱节点 hover 显示标题+笔记+摘要节选，点击开插件内面板（可编辑笔记/摘要注记）。研究者/方向笔记中的代表作以「标题（年份）[PubMed]」引用。

### 论文笔记（P2：元数据 + 方向角色锚定句）
```markdown
# <标题>

> 2006 · Nature · 被引 **2762** · ⚠️ 已撤稿
[PubMed](…) · DOI: …

**作者**：[[BG000007|Sylvain Lesné]]、…   ← 仅已导出研究者加 wikilink，其余纯文本

**方向角色**
> <从方向快照提取的、明确锚定该 paper_id 的句子，1-3 句>
> — 出处：[[direction-9351|<方向名>]] 方向快照
```
方向角色保证**正确针对该论文**：句子来自方向快照/作者画像中锚定该 paper_id 的原文，
非代表论文无快照提及时退化为元数据壳（不硬凑）。

### 事件笔记
状态 + 来源 URL + 论文级标记/人员级标记 wikilink。

### 人工编辑区（导出不覆盖）
- **frontmatter `manual_*` 键**：`manual_note`/`manual_name_zh`/`manual_paper_note` 等——人工写入，
  export_vault 保留；`notes_sync apply` 按白名单回写（见 SKILL 工作流 9）
- **正文人工批注块**：`<details class="pp-note"><summary>📝 人工批注（本区导出不覆盖）</summary>…</details>`
  ——Obsidian 阅读可见可编辑，导出不覆盖
- 画像/方向正文修订：直接改正文会被下次导出重写；如需持久请用批注区或经 notes_sync/裁决回写权威层

## 五、链接跳转规则

- **vault 笔记内的 wikilink 走 Obsidian 原生**（点击打开对应 md 阅读视图），插件不劫持点击。
- **插件视图内**的点击在视图内渲染（档案/下钻），不跳出。
- 双击 `<domain>.peopar` → 插件文件视图打开该域；ribbon/命令 → 插件视图（默认域）。
- 从笔记出发与从插件出发到达同一份信息（frontmatter + 正文），不冲突。

## 六、插件读取边界

- 读：`<方向名>/peopar/` 下 `type ∈ {direction, researcher, paper, event}` 的 md + `_sync.md` + `*.peopar`。
- 不读：vault 其他任何文件；不写入用户普通笔记（实时模式写操作只改 SQLite）。
- 用户笔记里的 `[[BG000117]]` 是普通 wikilink，插件不干预。
- Obsidian 原生能力（搜索/双链/标签/Dataview/Database）对投影文件全部可用；Dataview 查 `type` frontmatter 即可（不加 Obsidian 标签）。

## 七、研究者"研究方向"的正确性保证（三层构成）

1. **所属方向**（确定性：共著聚类 + LLM 命名 + 人工审阅）——无 LLM 画像也正确
2. **代表作**（确定性：论文事实，wikilink）
3. **LLM 画像 focus/summary**（增强层：锚定校验 + 待审，`review=approved` 才无歧义展示）

## 八、新方向的仓库决策规则（应用层面）

| 判断维度 | 补进原库（新域） | 新建独立仓库 |
|---|---|---|
| 作者重叠度 | 与现有域作者高度重叠（同一批人）→ 同库（BG 身份/画像/标记一次维护） | 基本零重叠 → 独立（身份断裂无损失） |
| 数据源 | 同为 PubMed/OpenAlex 主力 | 依赖全新源 |
| 图谱规模 | 当前库 12.8 万作者；增量小 | 新增 10 万+ 作者 |
| 管理边界 | 造假事件/噪声簇需跨域联动 | 完全独立 |

- 同领域新方向（作者重叠高）→ `python3 manage/domains.py new` 补进原库（默认）
- 跨学科新领域（作者零重叠、源独立）→ 新建独立 peopar 仓库
- 判断流程：先统计目标方向作者与现有域作者的重叠比例，≥阈值（建议 20%）同库，否则独立。

## 九、后续增强（已记录未实施）

- 插件检索框增加「在线检索」（PubMed/OpenAlex，经本地服务代理或直接外链）——用户已提出。
