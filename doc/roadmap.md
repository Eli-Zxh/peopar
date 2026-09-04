# 百官行述 · peopar 项目路线图（TodoList）

> 状态：2026-09（第三版改进后）。约定：代码/规范里程碑打 tag；内容生产不打 tag（持续、待审）。

## ✅ 已完成

### 基建与定位
- [x] MVP→二期：采集（PubMed/OpenAlex 确定性规则）、图谱聚类、造假闭环、域管理
- [x] 定位调整（v0.2.0）：Obsidian 插件第一入口、webvpn skill 化、LLM 合成闭环（方向/作者/域级）、方向第一视角
- [x] 三层架构（v0.2.1）：L1 SQLite+CLI / L2 export_vault 投影 / L3 插件静态优先 + `.peopar` 触发 + 文件监听
- [x] 数据修复：pubmed `--full` EDAT 分片；neuroling 58045 篇历史 + OpenAlex 20.5 万作者关联

### 第三版改进（进行中/已代码化）
- [x] 图谱数学模型（doc/visualization-model.md v0.1）
- [x] LLM 布局评分：direction_map 66 对 + direction_affinity 240 篇（已入待审）｜✅ 首批
- [x] 布局求解器 analyze/layout.py + node_layout 落库 + /api/layout + vault `_layout/` 导出
- [x] 插件信息化方向图谱（方向区域+论文/作者散点+连线，点击下钻/档案/论文）
- [x] 人工编辑：export 保留 manual_* 与 📝 人工批注块；notes_sync scan/apply 回写（白名单）
- [x] 标签体系：tag_vocab 32 词种子 + manage/tags.py（seed/propose/review/suggest/list）
- [x] 研究者深描模板 prompts/researcher_deepdive.md + SKILL 工作流 8
- [x] SKILL v2.2：布局评分/编辑同步/深描/仓库决策规则

## 🔜 内容生产（skill 会话；产物全部待审）

- [ ] 布局评分**人工批准**（layout_scores review；批准后布局正式生效，现用 --include-pending 预演）
- [ ] 未命名主要方向快照补齐（neuroling 13 个：extract pack → cluster_snapshot 合成 → apply）
- [ ] 方向级叙述覆盖 ad_lesne（簇 2-6 噪声裁决后余簇）
- [ ] ad_lesne 布局（单方向无 dir_sim；可仅做方向内论文/作者布局）
- [ ] 研究者画像成体系（核心层 5258 人分 132 包，workflow + author_profile；首批 2 人已示范）
- [ ] 研究者深描示范（手动触发单研究者 → tags/affiliations/画像）
- [ ] 域级概览笔记（domain_overview）

## 🛠 后续增强（未实施）

- [ ] 插件检索框「在线检索」（PubMed/OpenAlex 外链/服务代理）——用户已提
- [ ] 插件编辑 UI：管理台「变更请求队列」「标签批准」「布局评分批准」卡片；档案/论文页 📝 批注快捷编辑
- [ ] 插件子折叠页细化（方向页研究者在页内展开论文/批注折叠）
- [ ] 结构性变更请求（change_requests 表已建）：补/删论文、标记修正的裁决流 API+UI
- [ ] 图谱下钻增强：次方向偏移虚线连线渲染、区域聚焦动画
- [ ] 浏览器版 web/index.html 同步信息化图谱（可选）
- [ ] 观察者/关注视图（是否再议——研究者深描为手动，未含被动追踪）
- [ ] tests/ 回归 + 命名规范化收尾（设计文档 D 批次）
- [ ] export_vault 性能（2500+ 研究者/5800 论文全量导出已 OK；增量导出策略可选）

## 发布记录

- v0.2.0 定位调整（三层架构前）
- v0.2.1（commit 层）三层架构 + vault 投影 + .peopar
- v0.3.0（下一里程碑）：信息化图谱 + 编辑机制 + 标签/深描 代码完成
  （评分批准、方向补齐、深描示范属内容生产，随 skill 会话完成后合入，不单独打 tag）
