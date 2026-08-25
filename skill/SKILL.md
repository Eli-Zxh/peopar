---
name: peopar
description: 操作百官行述/peopar 研究者图谱系统（~/Documents/script/peopar）。当用户提到百官行述、peopar、研究者图谱、新增研究领域/学科域、文献增量更新、造假标记、撤稿事件、汉字名校对时使用。涵盖建域、采集管线、造假闭环与常见陷阱。
version: 1.0.0
---

# 百官行述 · peopar 操作手册

以研究者为中心的学术图谱：共著网络 + 方向簇 + 造假标记闭环。零依赖（Python 标准库 + SQLite + ECharts），本地应用 `python3 app.py` → http://127.0.0.1:8765。

## 不可违反的原则

1. 研究者主键为系统自建 `BG######`；外部 ID（OpenAlex/ORCID/PubMed）只是归并线索，入别名表。
2. 论文收录判定 = 确定性规则（MeSH ∪ 强关键词 ∪ 边界词共现 ∪ 引用扩散），**AI 绝不参与收录判定**。
3. **L0（确认造假）必须用户人工定性**；自动标记止步于 L1 风险提示；共同作者≠共谋。AI 不得代为确认任何 L0 或事件状态。
4. 方向簇只增不改：禁止手工 UPDATE clusters/历史 batch。
5. 一切写入留审计（audit_log）；代用户执行写操作时 actor 用 `user:<署名>` 并先向用户确认。

## 命令速查

```
启动应用          python3 app.py                       # 127.0.0.1:8765
增量更新(全)      ./update.sh
域管理            python3 manage/domains.py new|list|check|remove
PubMed 采集       python3 ingest/pubmed.py <域id> [--incremental] [--limit N] [--dry-run]
OpenAlex 回填     python3 ingest/openalex.py <域id>
图谱刷新          python3 analyze/graph.py <域id>
造假事件          python3 manage/events.py apply <json> | list | confirm-l0 | confirm-event
```

## 工作流 1：新增研究领域（AI 核心参与点）

用户给出方向描述后：
1. 起草词表：`mesh_terms`（精确 MeSH 词）、`mesh_boundary`（过宽词，如 Language）、`keywords_strong`（tiab 短语，支持 * 通配）、`cooccur_terms`（语境约束词）、`author_queries`（仅锚定人物的范例域用）。种子论文放 `seeds.seed_dois` 触发引用扩散。
2. `python3 manage/domains.py new <id> --name ... --mesh ... --keywords-strong ...`
3. `python3 manage/domains.py check <id>` —— 所有 MeSH 词必须有效；无效词用 esearch `db=mesh` 或 https://meshb.nlm.nih.gov/ 查正确拼写/层级后改配置再验。
4. 先小量试跑：`python3 ingest/pubmed.py <id> --limit 100`，抽查库里标题是否合域；再全量。
5. `ingest/openalex.py <id>` → `analyze/graph.py <id>`，浏览器看图：大簇的 top 作者是否是该领域真实活跃团队（这是聚类质量的判据）。

## 工作流 2：增量更新

`./update.sh`（PubMed 按 EDAT 窗口增量，不重拉全量）。建议周期：免费源每日、订阅/中文源每周、图谱全量刷新每月。

## 工作流 3：登记造假事件

1. 收集来源 URL（Retraction Watch / PubPeer / 新闻调查）。
2. 仿 `data/lesne_event.json` 起草 `data/<slug>_event.json`：title、description、source_urls、l0_candidates（name_query 须与库内 name_display 精确一致，可先 SQL 查）。
3. `python3 manage/events.py apply data/<slug>_event.json` —— 自动挂接当事作者的 PubMed 权威撤稿/更正标记，自动生成共著者 L1 提示。
4. **把 L0 确认与事件确认留给用户在网页上做**（事件页有按钮）；AI 只整理材料。

## 工作流 4：校对辅助

管理台队列 = 未验证别名。AI 可协助查机构主页/论文签名给汉字名候选，但确认/驳回动作由用户执行（或用户明确授意后调 API，by=<用户署名>）。

## 陷阱（实测踩过）

- PubMed esearch 返回 JSON 可能含控制字符：fetch 已用 `strict=False`，勿改回。
- OpenAlex：`select` 无 `pmid` 字段，用 `ids`；`per-page` 上限 200，更多用 `cursor=*` 分页；filter 里多值用 `|`。
- E-utilities 无 API key 限 3 req/s，代码已内置 sleep，勿删。
- 单篇 >25 作者（联盟论文）不生成全对共著边。
- 单篇外围作者默认不上图（在库中保留）。
- 库文件在 `data/peopar.db`（WAL 模式）；备份 = 复制该文件。

## 验证

`sqlite3 data/peopar.db "SELECT COUNT(*) FROM papers"` 等；浏览器逐页查看；审计：管理台「操作留痕」。
