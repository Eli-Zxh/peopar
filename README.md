# 百官行述 · peopar

> 一个以**研究者**为中心的学术图谱系统：建立人员关系网络、归纳研究方向、标记与修订学术造假影响面。
> 名字取自清代人物传记集《百官行述》——为当代"百官"（研究者）立传、存真、辨伪。

## 为什么做这个

学科扩张太快，两个问题长期无解：

1. **脉络难建**——读论文看不见学界整体思路，热点更新跟不上；
2. **纠错太弱**——学术造假出现后，被误导的方向难以被系统性标记与修正。

本系统以神经病学（阿尔茨海默病 + 神经语言学）为首个示例领域，但架构面向**任意学科**：一切收录规则由配置文件驱动。

## 核心特性

- **研究者实体库**：系统自建主键（`BG######`），外部 ID（OpenAlex / ORCID / PubMed）仅作归并线索；全局别名表（汉字真名 / 拼音 / 署名变体）；双层制（核心层完整档案 + 外围层保连通）。
- **共著关系图谱**：论文→作者→共著边，纯标准库标签传播社区发现，方向簇物化存档。
- **造假标记闭环**：事件状态机（疑似→核实中→已确认），论文级标记（撤稿 / 更正 / 关注声明）、人员级分级（**L0=确认造假，必须人工定性**；L1=共著风险提示，绝非定性）、受影响结论修订与全程审计留痕。
- **簇只增不改**：历史方向不因增量更新被重算，保证可追溯。
- **零依赖**：仅 Python 标准库 + SQLite + ECharts，无需安装任何第三方包，无需联网服务。

## 快速开始

```bash
python3 app.py          # 启动 → http://127.0.0.1:8765
./update.sh             # 增量更新（免费源）
```

四个页面：**图谱总览**（红圈=L0、橙点=L1，点击开档案）、**研究者档案**（履历时间线 / 论文 / 合作者）、**造假事件**、**管理台**（汉字名校对队列 + 审计日志）。

## 扩展任意新领域

收录规则全部在配置里，三步即可接入一个新学科：

```bash
# 1. 新建域（词表可用 AI 起草后人工核对）
python3 manage/domains.py new migraine --name "偏头痛" \
    --mesh "Migraine Disorders" "Migraine with Aura" \
    --keywords-strong "migraine"

# 2. 校验 MeSH 词（对照 NLM 官方词库）
python3 manage/domains.py check migraine

# 3. 采集 → 回填 → 图谱
python3 ingest/pubmed.py migraine
python3 ingest/openalex.py migraine
python3 analyze/graph.py migraine
```

## 数据源

| 层 | 来源 | 通道 |
|---|---|---|
| A | PubMed (E-utilities)、OpenAlex | 免费直连 |
| B | Web of Science / Scopus / CNKI / 万方 | 机构 webvpn（预留，见二期） |
| C | Retraction Watch / PubPeer | 造假标注源 |

**论文收录判定为确定性规则**（MeSH 命中 ∪ 强关键词 ∪ 边界词共现 ∪ 引用种子扩散），可复现、可审计，**LLM 不参与收录判定**。

## 目录结构

```
app.py                  零依赖本地 Web 应用（http.server + SQLite + ECharts）
schema.sql              实体模型（研究者/论文/簇/造假事件/审计）
config/domains/         各域收录规则（_template.json 为模板）
ingest/                 采集层：pubmed / openalex
analyze/                图谱与聚类
manage/                 domains.py（域管理）、events.py（造假事件）
web/ static/            前端与 ECharts
data/                   SQLite 库、图谱 JSON、事件种子
skill/SKILL.md          面向 AI 助手的操作手册
```

## 二期路线

- B/C 层源接入（机构 webvpn，浏览器自动化）；
- LLM 方向快照（定义 / 代表文献 / 时间线 / 分歧点，引用锚定防幻觉，人工在环）；
- 定时增量（免费源每日 / 订阅与中文源每周 / 核心分析每月）。

## 原则声明

数据来自公开学术 API；**L0 造假定性必须人工确认**，系统不做有罪推定，共同作者不默认共谋；一切结论、履历、标记可下钻到来源与依据。

## License

MIT
