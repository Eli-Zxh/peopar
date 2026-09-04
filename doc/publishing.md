# 发布与分发规范（插件 / skill / Agent 文档）

> 面向 Agent 与维护者的发布操作说明。版本基线见 `git tag v0.2.0`。

## 一、Obsidian 插件（社区插件发布规范）

### 本地安装（开发/个人使用，已配置）
1. 构建：`cd obsidian-plugin && npm install && node esbuild.config.mjs production`
2. 产物自动复制 `src/styles.css` → `styles.css`；main.js 在仓库根。
3. symlink 进仓库：`ln -sfn <repo>/obsidian-plugin "<vault>/.obsidian/plugins/peopar"`
4. Obsidian 设置 → 社区插件 → 重新加载/启用。

### 社区发布（提交到 Obsidian 社区插件库需满足）
仓库根（此处为 `obsidian-plugin/` 子目录，若独立发布需整目录上传为独立仓库）须含：
- `manifest.json`（id/name/version/minAppVersion/description/author/isDesktopOnly）
- `versions.json`（`{"插件版本": "最低 Obsidian 版本"}` 映射）——已提供
- `main.js`、`styles.css`（构建产物，勿手动编辑）
- 发布流程：为提交打 tag（如 `v0.2.0`）→ GitHub Release 附插件包（zip：main.js + manifest.json + styles.json + styles.css）
- 每次发布同步 bump `manifest.json` 与 `versions.json` 版本

### 插件给 Agent 的阅读规范
插件源码注释/文件即文档：`src/main.ts`（入口与数据源选择）、`src/vaultData.ts`（vault md 契约读取）、
`src/live.ts`（实时服务）、`src/views.ts`（视图与图表）、`src/panels.ts`（档案/事件/管理台渲染）。
**vault 文件格式契约以 `doc/obsidian-vault-format.md` 为准**（L2 投影层与 L3 插件共享的接口）。

## 二、Skill（AI 助手操作手册）

### 规范要求（Agent skill）
- 文件：`skill/SKILL.md`，frontmatter 含 `name` / `description`（触发词与范围）/ `version`。
- 指令须可执行、含红线与陷阱；命令速查与工作流编号。
- 给 Agent 使用方式：
  - Claude：放入 `~/.claude/skills/peopar/SKILL.md`（或项目 `.agents/skills/`）；
  - 或直接作为会话系统提示引用；本仓库内始终维护 `skill/SKILL.md` 为主源。
- 版本：SKILL.md frontmatter `version` 与插件/发布版本独立递增（当前 2.1 → 随本轮改进升 3.x）。

### Skill 给 Agent 的阅读入口
1. `skill/SKILL.md`（总纲 + 工作流 + 陷阱 + 三层架构 + 仓库决策规则）
2. `prompts/*.md`（方向/作者/域级/方向研究者合成模板，versioned）
3. `doc/obsidian-vault-format.md`（vault 文件格式契约）
4. `doc/positioning-20260827.md`（定位调整决策记录）
5. `README.md`（架构总览）

## 三、Release（GitHub）

- tag 即版本（annotated）：`git tag -a v0.2.0 -m "..." && git push origin v0.2.0`
- GitHub Release 对象可在网页为 tag 创建（附插件 zip 与 changelog）；当前环境无 `gh` CLI，采用 web 创建。
- changelog 结构：定位调整 → 三层架构 → 本轮改进（图谱/可编辑/方向与研究者体系）→ 数据基线。

## 四、版本节奏建议

- 内容生产（方向/作者/域级合成、数据更新）**不打 tag**——持续进行、产物待审。
- 代码/规范里程碑（架构、UI、发布面改动）打 tag：`v0.3.0`（本轮改进）等。
