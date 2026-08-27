# 定位调整记录 · 2026-08-27（P1–P4 已执行）

> 本文档记录本次定位调整的依据、决策与交付物，供后续回溯与审阅。

## 一、调整目标（用户确认的方案要点）

1. **更新方式 skill 化**：机构 webvpn 环境下半自动更新（用户手动登录 → AI 助手检索导出 → 脚本导入）；无常驻进程、不自动调度。
2. **LLM 归纳研究者/方向/热点**：方向按实际研究思路命名（研究方向分簇），每个方向列出该方向研究者（谁在做这个方向）；可视化以方向为第一视角 + 方向→研究者 + 热点时间演化。
3. **Obsidian 原生插件**：替换浏览器网页为主要前端（新拟态 + 柔和粉彩 UI，取缔蓝白扁平风）；浏览器版保留。

## 二、决策记录（问答摘要）

| 决策点 | 结论 |
|---|---|
| webvpn 形态 | SSL VPN 门户（URL 重写代理）；skill 指导 AI 助手操作浏览器/curl 收集 |
| 更新调度 | 手动触发 + skill 化（无保活进程） |
| 插件形态 | 原生 TypeScript 插件重写前端，调本地 API；插件自动拉起 python3 app.py |
| LLM 运行 | skill 式（AI 助手按版本化模板合成，人工在环；无 API key） |
| 可视化侧重 | 方向第一视角（聚合图+下钻）、方向→研究者、热点时间演化（多选） |
| 可联系研究者 | 方向簇按研究思路命名 → 簇内研究者清单；核心作者机构官网信息抓取校验 |
| UI 参考 | 新拟态元素 + 柔和粉彩（参考 ui-ux-pro-max-skill mental-health demo） |
| 目标 vault | /Users/zhangxinhao/Documents/Obisidian Valut |
| 图谱形态 | 方向聚合图（簇为节点）+ 下钻作者层 |

## 三、交付物

- **后端扩展（P1）**：`schema.sql`（author_snapshots/judgments/webvpn_imports/指纹/display）、`ingest/common.py` 迁移、
  `ingest/webvpn_import.py`（Scopus CSV / CNKI / 万方 RIS）、`manage/webvpn.py`、`manage/affiliations.py`、
  `manage/judgment.py`、`manage/snapshot.py`（apply-authors/staleness/list 双类型）、`analyze/extract.py --authors`、
  `app.py` 新 API（/api/directions、/api/trends、/api/direction/<id>/researchers、/api/author/<id>/snapshot、
  /api/judgments+decide、/api/webvpn/import+imports、/api/affiliation-queue+verify、/api/health、/api/author-snapshot/<id>/review）。
- **Obsidian 插件（P3）**：`obsidian-plugin/`（TypeScript + esbuild + ECharts bundle；视图：方向图谱/方向·研究者/热点时间线/
  研究者档案/造假事件/管理台；styles.css 新拟态粉彩，深浅自适应）；已 symlink 进 vault 并注册 community-plugins。
- **skill 与文档（P4）**：`skill/SKILL.md` v2、`prompts/author_profile.md`、`prompts/direction_researchers.md`、
  `prompts/webvpn_collect.md`、`update_webvpn.sh`、`misc/com.peopar.update.plist`（可选定时模板）、README 更新。
- **内容生产（P2）**：ad_lesne 簇 1 方向快照 v2（supersedes v1）+ 3 条噪声簇提案（腈水合酶/血红蛋白/蛋白酶体）+ 3 人作者画像首批；
  neuroling 方向级/作者级合成在本记录后由 skill 会话继续（数据已全量修复）。

## 四、数据修复（执行中发现并处理）

- **问题**：neuroling 首次全量采集受 `sort:pub_date` + `max_fetch=10000` 限制，PubMed 命中 22.9 万条只取了最新 1 万条，
  历史论文从未入库（此前 7670 篇全部为 2024 年后）。方向簇/快照/可视化均基于残缺数据。
- **修复**：`ingest/pubmed.py` 新增 `--full` 模式（EDAT 年份窗口分片 + 迭代二分，覆盖 1900–今）；
  网络重试扩展捕获 IncompleteRead/ConnectionError；已重跑 neuroling 全量（历史论文持续入库中）。
- **教训**：新增域的首次全量必须用 `--full`（已写入 SKILL.md 陷阱与 README）。

## 五、红线（保持）

收录判定无 LLM；L0 造假定性仅人工；方向簇只增不改（display=excluded 仅展示层）；LLM 产物锚定校验 + 待审；全程审计留痕。

## 六、后续建议

1. neuroling 全量采集完成后：`analyze/graph.py neuroling` 刷新图谱 → `analyze/extract.py neuroling` 重新出包 →
   按 `prompts/cluster_snapshot.md` 合成主簇方向快照 → `manage/snapshot.py apply`；再 `--authors` 出核心层画像包 →
   按 `prompts/author_profile.md` 合成 → `apply-authors`。
2. Obsidian 中启用「百官行述 · 研究者图谱」（ribbon/命令面板打开）；本地服务由插件自动拉起。
3. webvpn 采集：按 `prompts/webvpn_collect.md` 流程（Scopus/CNKI/万方）。
4. 浏览器版 web/index.html 保留兼容，可后续同步新风格。
