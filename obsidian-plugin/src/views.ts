import { ItemView, FileView, Notice, TFile, Component, MarkdownRenderer } from "obsidian";
import * as echarts from "echarts";
import { VIEW_TYPE, FILE_VIEW_TYPE } from "./main";
import type PeoparPlugin from "./main";
import {
  DataProvider, Domain, DirectionsResp, TrendsResp, Direction, AuthorDetail,
  AuthorSnapshotResp, Researcher, DirectionResp, Inst,
} from "./api";
import { LiveProvider } from "./live";

export const PALETTE = ["#8B7CF6", "#5FA8D3", "#7BC8B4", "#F0A08C", "#E98AA8", "#A6C06A",
  "#7FA6C9", "#D9A8E0", "#8BC8A8", "#E8B26A", "#6FB3C9", "#C98BB0"];

const esc = (s: any): string => (s ?? "").toString().replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
export { esc };

export function flagBadges(f: any): string {
  let h = "";
  if (f?.retraction_status === "retracted") h += '<span class="pp-badge pp-b-retracted">撤稿</span>';
  if (f?.retraction_status === "corrected") h += '<span class="pp-badge pp-b-corrected">更正</span>';
  if (f?.retraction_status === "concern") h += '<span class="pp-badge pp-b-corrected">关注</span>';
  return h;
}
export function statusBadge(s: string): string {
  const m: Record<string, string> = { suspected: "疑似", verifying: "核实中", confirmed: "已确认",
    dismissed: "已排除", pending: "待审", approved: "已审", rejected: "驳回", accepted: "已采纳" };
  return `<span class="pp-badge pp-b-${s}">${m[s] ?? esc(s)}</span>`;
}
export function dirName(d: { name?: string | null; label?: number }): string {
  return d?.name ? esc(d.name) : `方向 #${d?.label ?? "?"}`;
}

/** 视图核心：渲染逻辑与数据来源（provider）解耦；AtlasView 与 PeoparFileView 共用。 */
export class AtlasApp {
  plugin: PeoparPlugin;
  el: HTMLElement;
  provider: DataProvider;
  user: string = "user";
  domain: string = "";
  dirs: DirectionsResp | null = null;
  trends: TrendsResp | null = null;
  charts: echarts.ECharts[] = [];
  drillCid: number | null = null;
  private offData: (() => void) | null = null;

  constructor(plugin: PeoparPlugin, el: HTMLElement) {
    this.plugin = plugin;
    this.el = el;
    this.provider = plugin.provider;
  }

  get live(): LiveProvider | null { return this.provider instanceof LiveProvider ? this.provider : null; }

  async mount(defaultDomain?: string) {
    const c = this.el;
    c.empty();
    c.addClass("pp-root");
    c.innerHTML = `
    <div class="pp-header">
      <div class="pp-title">百官行述 <span class="pp-sub">Researcher Atlas</span></div>
      <select class="pp-domain"></select>
      <div class="pp-searchwrap"><input class="pp-q" placeholder="检索：姓名 / 论文…">
        <div class="pp-qres" style="display:none"></div></div>
      <nav class="pp-nav">
        <button data-tab="graph" class="on">方向图谱</button>
        <button data-tab="researchers">方向·研究者</button>
        <button data-tab="trends">热点时间线</button>
        <button data-tab="author">研究者档案</button>
        <button data-tab="events">造假事件</button>
      </nav>
      <span class="pp-sync" title="数据来源与同步时间"></span>
    </div>
    <main class="pp-main">
      <section data-sec="graph">
        <div class="pp-stats"></div>
        <div class="pp-row">
          <div class="pp-side pp-card" id="pp-dirlist"></div>
          <div class="pp-grow pp-card pp-chartbox"><div class="pp-chart" id="pp-dirchart"></div></div>
        </div>
        <div class="pp-drill" id="pp-drill" style="display:none"></div>
      </section>
      <section data-sec="researchers" style="display:none"></section>
      <section data-sec="trends" style="display:none"></section>
      <section data-sec="author" style="display:none"></section>
      <section data-sec="events" style="display:none"></section>
    </main>`;

    c.querySelectorAll(".pp-nav button").forEach(b => b.addEventListener("click", () => this.showTab((b as HTMLElement).dataset.tab!)));
    (c.querySelector(".pp-domain") as HTMLSelectElement).addEventListener("change", e => {
      this.domain = (e.target as HTMLSelectElement).value;
      this.loadGraph();
    });
    this.setupSearch();
    this.offData = this.plugin.onDataChange(() => { if (this.domain) this.loadGraph(); });
    this.renderSync();
    await this.loadDomains(defaultDomain);
  }

  dispose() {
    this.charts.forEach(ch => ch.dispose());
    this.charts = [];
    if (this.offData) this.offData();
  }

  renderSync() {
    const el = this.el.querySelector(".pp-sync") as HTMLElement;
    if (this.provider.serverConnected()) {
      el.textContent = "● 实时（本地服务）";
      el.classList.add("pp-sync-live");
    } else {
      const t = this.provider.lastSync();
      el.textContent = "○ vault 快照" + (t ? ` · ${t.slice(5, 16)}` : "");
      el.classList.remove("pp-sync-live");
    }
  }

  showTab(tab: string) {
    this.el.querySelectorAll(".pp-nav button").forEach(b =>
      b.classList.toggle("on", (b as HTMLElement).dataset.tab === tab));
    this.el.querySelectorAll("main section[data-sec]").forEach(s =>
      (s as HTMLElement).style.display = (s as HTMLElement).dataset.sec === tab ? "" : "none");
    if (tab === "researchers") this.loadResearchers();
    if (tab === "trends") this.loadTrends();
    if (tab === "events") this.loadEvents();
    if (tab === "graph") setTimeout(() => this.charts.forEach(ch => ch.resize()), 60);
  }

  // ---------- 域与方向聚合图 ----------
  async loadDomains(defaultDomain?: string) {
    const ds = await this.provider.domains();
    const sel = this.el.querySelector(".pp-domain") as HTMLSelectElement;
    sel.innerHTML = ds.map(d => `<option value="${esc(d.id)}">${esc(d.name)}（${d.papers} 篇 / ${d.authors} 人）</option>`).join("");
    const want = defaultDomain ? ds.find(d => d.id === defaultDomain) : undefined;
    this.domain = want?.id || ds.find(d => d.graph_ready)?.id || ds[0]?.id || "";
    if (this.domain) this.loadGraph();
  }

  async loadGraph() {
    this.dirs = await this.provider.directions(this.domain);
    const ds = this.dirs.directions;
    const q = this.el.querySelector(".pp-stats")!;
    q.innerHTML = `
      <span class="pp-stat"><b>${ds.length}</b> 主要方向</span>
      <span class="pp-stat"><b>${ds.filter(d => d.name).length}</b> 已命名</span>
      <span class="pp-stat"><b>${ds.reduce((s, d) => s + d.size, 0)}</b> 研究者</span>
      <span class="pp-stat"><b>${ds.reduce((s, d) => s + d.recent, 0)}</b> 近三年论文</span>
      <span class="pp-hint">滚轮/拖拽缩放 · 论文=散点(点击看摘要与编辑) · ◇=研究者(点击档案) · 圆=方向(点击笔记) · 连线可经图例开关</span>`;
    this.renderDirList(ds);
    const layout = await this.provider.layout(this.domain).catch(() => null);
    const chartBox = this.el.querySelector("#pp-dirchart") as HTMLElement;
    if (layout && layout.directions?.length) {
      this.drawInfoGraph(layout);
    } else {
      chartBox.innerHTML = '<div class="pp-meta" style="padding:30px">暂无布局数据：运行 <code>python3 analyze/layout.py ' + this.domain +
        ' --out data/layout_' + this.domain + '.json</code> 后重新导出。</div>';
    }
  }

  /** 信息化方向图谱：方向区域 + 论文/研究者散点 + 作者连线（关联距离预计算布局） */
  drawInfoGraph(g: any) {
    const el = this.el.querySelector("#pp-dirchart") as HTMLElement;
    const chart = echarts.init(el);
    this.charts.push(chart);
    const byCluster = new Map<number, number>();
    g.directions.forEach((d: any, i: number) => byCluster.set(d.cluster_id, i));
    const dirColor = (cid: number) => PALETTE[(byCluster.get(cid) ?? 0) % PALETTE.length];
    const dirmap = new Map<number, any>(g.directions.map((d: any) => [d.cluster_id, d]));
    const paperById = new Map<string, any>(g.papers.map((p: any) => [p.id, p]));
    const authById = new Map<string, any>(g.authors.map((a: any) => [a.id, a]));
    // ---- 尺寸约束（相对画布） ----
    const maxR = Math.max(...g.directions.map((d: any) => Math.hypot(d.x, d.y) + d.r), 1);
    const s = 330 / maxR;                       // 坐标→像素比例（区域直径 ≤ ~0.5 画布高）
    const dirSize = (d: any) => Math.max(64, Math.min(230, d.r * 2 * s * 0.62));
    const paperSize = (p: any) => Math.max(3.2, Math.min(7.5, 2.4 + (p.r - 5) * 0.16));
    const authorSize = (a: any) => Math.max(10, Math.min(20, a.r * 0.9));
    // ---- 区域（半透明包络圆，直径反映簇规模） ----
    const dirNodes = g.directions.map((d: any) => ({
      value: [d.x, d.y], symbolSize: dirSize(d), cluster_id: d.cluster_id, _d: d,
      itemStyle: { color: dirColor(d.cluster_id), opacity: 0.13, borderColor: dirColor(d.cluster_id), borderWidth: 2 },
      label: { show: true, formatter: () => (d.name || "").slice(0, 20), fontSize: 11, fontWeight: 600,
        color: "#4a4265", position: "top" },
    }));
    // ---- 连线（三型） ----
    const mkLine = (kind: string, style: any) => g.edges.filter((e: any) => e.kind === kind).map((e: any) => {
      if (kind === "crossdir") { const sr: any = paperById.get(e.source);
        return { coords: [[sr?.x ?? 0, sr?.y ?? 0], [e.target_x, e.target_y]] }; }
      const s2 = paperById.get(e.source) ?? authById.get(e.source);
      const t2 = paperById.get(e.target);
      return s2 && t2 ? { coords: [[s2.x, s2.y], [t2.x, t2.y]] } : null;
    }).filter(Boolean).map((c: any) => ({ ...c, lineStyle: { ...style, width: style.width } }));
    const authored = mkLine("authored", { color: "#8f84c4", width: 0.9, opacity: 0.5 });
    const cowrite = mkLine("cowrite", { color: "#cfc8e8", width: 0.6, opacity: 0.35 });
    const crossdir = mkLine("crossdir", { color: "#c09a6a", width: 1.1, opacity: 0.7, type: "dashed" });
    // ---- 论文（小散点，色深/亮度表达重要性） ----
    const paperNodes = g.papers.map((p: any) => ({
      value: [p.x, p.y], symbolSize: paperSize(p), cluster_id: p.cluster_id, _p: p,
      itemStyle: { color: dirColor(p.cluster_id),
        opacity: p.affinity == null ? 0.35 : 0.5 + (p.affinity ?? 0) * 0.5,
        borderColor: "#fff", borderWidth: p.affinity != null ? 1 : 0 },
      label: { show: (p.affinity ?? 0) >= 0.93 && p.title,
        formatter: () => (p.title || "").slice(0, 14), fontSize: 8, color: "#8b83a0" },
    }));
    const authorNodes = g.authors.map((a: any) => ({
      value: [a.x, a.y], symbolSize: authorSize(a), symbol: "diamond", cluster_id: a.cluster_id, _a: a,
      itemStyle: { color: dirColor(a.cluster_id), opacity: 0.95, borderColor: "#fff", borderWidth: 1.5 },
      label: { show: authorSize(a) >= 13, formatter: () => (a.name || "").slice(0, 9), fontSize: 9, color: "#4a4265" },
    }));
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    g.directions.forEach((d: any) => {
      x0 = Math.min(x0, d.x - d.r); x1 = Math.max(x1, d.x + d.r);
      y0 = Math.min(y0, d.y - d.r); y1 = Math.max(y1, d.y + d.r);
    });
    const pad = 90;
    chart.setOption({
      dataZoom: [{ type: "inside", xAxisIndex: 0, yAxisIndex: 0, zoomLock: false }],
      toolbox: { right: 6, top: 6, feature: {
        dataZoom: { yAxisIndex: "none", title: { zoom: "缩放", back: "复位" } },
        restore: { title: "还原" },
      } },
      tooltip: { formatter: (p: any) => {
        if (p.seriesIndex === 5) {
          const a = authorNodes[p.dataIndex]?._a;
          return `<b>${esc(a?.name ?? "")}</b>${a?.zh ? `（${esc(a.zh)}）` : ""}<br><span style="color:#8b83a0">研究者 · 点击打开档案</span>`;
        }
        if (p.seriesIndex === 4) {
          const pp = paperNodes[p.dataIndex]?._p;
          const abs = (pp?.abstract || "").slice(0, 120);
          let h = `<b>${esc(pp?.title ?? "")}</b>`;
          if (pp?.note) h += `<br><span style="color:#b96a00">📝 ${esc(pp.note.slice(0, 90))}</span>`;
          if (abs) h += `<br><span style="color:#68727f">${esc(abs)}${abs.length >= 120 ? "…" : ""}</span>`;
          h += `<br><span style="color:#8b83a0">被引 ${pp?.cite ?? 0}${pp?.affinity != null ? " · 关联 " + pp.affinity.toFixed(2) : ""} · 点击编辑/查看</span>`;
          return h;
        }
        if (p.seriesIndex === 0) {
          const d = dirNodes[p.dataIndex]?._d;
          return `<b>${esc(d?.name ?? "")}</b><br>规模 ${d?.size ?? ""}<br><span style="color:#8b83a0">点击查看该方向研究者</span>`;
        }
        return "";
      } },
      legend: { bottom: 0, textStyle: { fontSize: 10 }, selected: { "作者归属": true, "跨方向关联": true, "论文共著": false },
        data: ["作者归属", "跨方向关联", "论文共著"] },
      grid: { left: 8, right: 8, top: 8, bottom: 26 },
      xAxis: { type: "value", min: x0 - pad, max: x1 + pad, axisLine: { show: false },
        axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
      yAxis: { type: "value", min: y0 - pad, max: y1 + pad, axisLine: { show: false },
        axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
      series: [
        { name: "方向区域", type: "scatter", data: dirNodes, z: 1, roam: true, scaleLimit: { min: 0.3, max: 8 } },
        { name: "作者归属", type: "lines", data: authored, z: 2, silent: true },
        { name: "论文共著", type: "lines", data: cowrite, z: 2, silent: true },
        { name: "跨方向关联", type: "lines", data: crossdir, z: 2, silent: true },
        { name: "论文", type: "scatter", data: paperNodes, z: 3, roam: true },
        { name: "研究者", type: "scatter", data: authorNodes, z: 4, roam: true },
      ],
    });
    chart.on("click", (p: any) => {
      if (p.seriesIndex === 5) {
        const a = authorNodes[p.dataIndex]?._a;
        if (a?.id) this.openAuthor(a.id);
      } else if (p.seriesIndex === 4) {
        const pp = paperNodes[p.dataIndex]?._p;
        const pid = pp?.id?.replace("p:", "");
        if (pid) this.openPaper(pid, pp);
      } else if (p.seriesIndex === 0) {
        const d = dirNodes[p.dataIndex]?._d;
        if (d) this.drillDown(d.cluster_id);
      }
    });
  }

  /** 论文面板：显示 DB/节点数据（题目/期刊/摘要/笔记/原文链接/作者）并可编辑笔记与摘要注记 */
  async openPaper(pid: string, node?: any) {
    let d: any = node ? { title: node.title, abstract: node.abstract || "", note: node.note || "",
      pmid: node.pmid, cited: node.cite, authors: [] } : null;
    if (this.live) {
      try { d = await this.live.get(`/api/paper/${pid}`); } catch { /* 用节点数据兜底 */ }
    }
    if (!d || !d.title) { new Notice("论文信息不可用"); return; }
    const el = this.el.querySelector(".pp-main") as HTMLElement;
    const ov = el.createEl("div", { cls: "pp-overlay" });
    ov.innerHTML = `
      <div class="pp-card pp-paper-panel">
        <div class="pp-panel-head"><b>论文</b> <button class="pp-btn pp-btn-ghost pp-btn-sm" data-close>✕</button></div>
        <h3>${esc(d.title)}</h3>
        <div class="pp-meta">${d.year || ""} · ${esc(d.journal || "")} · 被引 <b>${d.cited ?? d.cite ?? 0}</b>
          ${d.retraction_status && d.retraction_status !== "none" ? ` · ⚠️ ${esc(d.retraction_status)}` : ""}
          ${d.pmid ? ` · <a class="pp-ext" href="https://pubmed.ncbi.nlm.nih.gov/${d.pmid}/" target="_blank">PubMed</a>` : ""}</div>
        ${(d.authors || []).length ? `<div class="pp-meta">作者：${(d.authors || []).map((x: any) => esc(x.name_display)).join("、")}</div>` : ""}
        <div class="pp-sec"><b>摘要${d.abstract_override ? "（人工注记）" : ""}</b>
          <div class="pp-meta">${esc(d.display_abstract || d.abstract || "（无摘要）")}</div></div>
        ${this.live ? `
          <div class="pp-sec"><b>📝 笔记（写回数据库）</b>
            <textarea id="pp-note" class="pp-input" rows="2">${esc(d.note || "")}</textarea>
            <div class="pp-sec"><b>摘要注记（覆盖显示，不改源摘要）</b>
              <textarea id="pp-absov" class="pp-input" rows="2">${esc(d.abstract_override || "")}</textarea></div>
            <button class="pp-btn pp-btn-primary" data-save>保存修订</button></div>`
        : `<div class="pp-sec"><b>📝 笔记</b><div class="pp-meta">${esc(d.note || "（无笔记）")}</div></div>
           <div class="pp-meta">启用「实时服务」后可在插件内编辑并写回数据库。</div>`}
        <div class="pp-sec pp-meta">论文 #${pid} · DB 权威存储 · vault 内不建散文件</div>
      </div>`;
    ov.querySelector("[data-close]")?.addEventListener("click", () => ov.remove());
    ov.addEventListener("click", (ev: MouseEvent) => { if (ev.target === ov) ov.remove(); });
    const save = ov.querySelector("[data-save]");
    save?.addEventListener("click", async () => {
      if (!this.live) return;
      await this.live.post(`/api/paper/${pid}/edit`, {
        note: (ov.querySelector("#pp-note") as HTMLTextAreaElement)?.value ?? null,
        abstract_override: (ov.querySelector("#pp-absov") as HTMLTextAreaElement)?.value ?? null,
        by: this.user,
      });
      new Notice("论文修订已写回数据库");
      ov.remove();
    });
  }

  renderDirList(ds: any[]) {
    const box = this.el.querySelector("#pp-dirlist")!;
    const named = ds.filter((d: any) => d.name);
    const unnamed = ds.filter((d: any) => !d.name);
    const item = (d: any) => `
      <div class="pp-diritem" data-cid="${d.cluster_id}">
        <span class="pp-dot" style="background:${PALETTE[(d.label ?? d.cluster_id) % PALETTE.length]}"></span>
        <b>${dirName(d)}</b>
        ${d.name ? (d.snap_review === "approved" ? '<span class="pp-badge pp-b-approved">已审</span>'
          : d.snap_review === "rejected" ? '<span class="pp-badge pp-b-rejected">驳回</span>'
          : '<span class="pp-badge pp-b-pending">待审</span>') : ""}
        <span class="pp-meta">${d.size} 人 · ${d.recent} 近文 · ${(d.top_authors || []).slice(0, 3).map((x: any) => esc(x.name)).join(" · ")}</span>
      </div>`;
    box.innerHTML = `<div class="pp-card-title">研究方向<span class="pp-meta">（点击查看研究者）</span></div>` +
      (named.length ? named.map(item).join("") : "") +
      (unnamed.length ? `<details class="pp-unamed"><summary class="pp-meta">未命名方向（${unnamed.length}）</summary>${unnamed.map(item).join("")}</details>` : "");
    box.querySelectorAll(".pp-diritem").forEach((el) => el.addEventListener("click", () => {
      this.drillDown(+(el as HTMLElement).dataset.cid!);
    }));
  }

  /** 点击研究方向 → 打开该方向的 vault 笔记（图谱区域与侧栏共用）。 */
  async drillDown(cid: number) {
    this.openDirection(cid);
  }

  private async renderMarkdown(mdText: string, into: HTMLElement) {
    const cm = new Component();
    await MarkdownRenderer.render(this.plugin.app, mdText, into, "", cm);
  }

  /** 方向笔记面板：渲染 vault 方向 md + 编辑入口 */
  async openDirection(cid: number) {
    const dirs = this.dirs?.directions ?? [];
    const d = dirs.find((x: any) => x.cluster_id === cid);
    const overlay = (this.el.querySelector(".pp-main") as HTMLElement).createEl("div", { cls: "pp-overlay" });
    const card = overlay.createEl("div", { cls: "pp-card pp-paper-panel" });
    const title = d?.name || `方向 #${cid}`;
    card.innerHTML = `<div class="pp-panel-head"><b>研究方向 · 笔记</b>
      <button class="pp-btn pp-btn-ghost pp-btn-sm" data-close>✕</button></div>
      <div class="pp-meta" style="margin-bottom:6px">
        <button class="pp-btn pp-btn-sm pp-btn-primary" data-edit>✎ 编辑方向笔记（Obsidian）</button>
        <button class="pp-btn pp-btn-sm pp-btn-ghost" data-persons>查看该方向研究者</button></div>`;
    const body = card.createDiv();
    const f = this.plugin.app.vault.getFiles().find(x => x.path.endsWith(`/directions/direction-${cid}.md`));
    if (f) {
      const mdText = await this.plugin.app.vault.adapter.read(f.path);
      await this.renderMarkdown(mdText, body);
    } else if (d) {
      body.innerHTML = `<h3>${esc(title)}</h3><div class="pp-meta">规模 ${d.size} · 论文 ${d.papers} ·
        未导出笔记（运行 export_vault 后生成）</div>`;
    }
    overlay.querySelector("[data-close]")?.addEventListener("click", () => overlay.remove());
    overlay.querySelector("[data-edit]")?.addEventListener("click", async () => {
      const fm = this.plugin.app.vault.getFiles().find(x => x.path.endsWith(`/directions/direction-${cid}.md`));
      if (fm) {
        const leaf = this.plugin.app.workspace.getLeaf(true);
        await leaf.openFile(fm);
        await leaf.setViewState({ type: "markdown", state: { file: fm.path, mode: "source" }, active: true });
      } else {
        new Notice("该方向笔记未在 vault（先运行 export_vault）");
      }
      overlay.remove();
    });
    overlay.querySelector("[data-persons]")?.addEventListener("click", () => {
      overlay.remove();
      this.showTab("researchers");
      this.renderResearchers(cid);
    });
    overlay.addEventListener("click", (ev: MouseEvent) => { if (ev.target === overlay) overlay.remove(); });
  }

  // ---------- 方向·研究者 ----------
  async loadResearchers() {
    const sec = this.el.querySelector('section[data-sec="researchers"]') as HTMLElement;
    if (!this.dirs) { sec.innerHTML = '<div class="pp-card pp-muted">请先在「方向图谱」加载</div>'; return; }
    const named = this.dirs.directions.filter(d => d.display !== "excluded");
    const sel = named.map(d => `<option value="${d.cluster_id}">${dirName(d)}（${d.size} 人）</option>`).join("");
    sec.innerHTML = `<div class="pp-card pp-researchers-head">
        <div class="pp-card-title">方向 → 研究者<span class="pp-meta">每个方向列出该方向的核心研究者</span></div>
        <select class="pp-dirsel">${sel}</select></div>
      <div class="pp-res-grid" id="pp-resgrid"></div>`;
    (sec.querySelector(".pp-dirsel") as HTMLSelectElement).addEventListener("change", e =>
      this.renderResearchers(+((e.target as HTMLSelectElement).value)));
    this.renderResearchers(named[0]?.cluster_id);
  }

  async renderResearchers(cid: number) {
    const grid = this.el.querySelector("#pp-resgrid") as HTMLElement;
    if (!cid) { grid.innerHTML = ""; return; }
    grid.innerHTML = '<div class="pp-muted">加载中…</div>';
    const d = await this.provider.directionResearchers(cid);
    const card = (r: Researcher) => `
      <div class="pp-card pp-r-card" data-aid="${esc(r.id)}">
        <div class="pp-r-name">${esc(r.name)} ${r.zh ? `<span class="pp-meta">（${esc(r.zh)}）</span>` : ""}
          ${r.tier === "core" ? '<span class="pp-badge pp-b-core">核心</span>' : ""}</div>
        ${r.institution ? `<div class="pp-meta">🏛 ${esc(r.institution.institution)}${r.institution.source_tag === "web" && r.institution.verified !== 1 ? ' <span class="pp-badge pp-b-pending">官网待校验</span>' : ""}</div>` : '<div class="pp-meta">🏛 机构待补</div>'}
        ${r.snapshot ? `<div class="pp-r-focus">${esc(r.snapshot.focus || "")}</div>
          <div class="pp-r-sum">${esc((r.snapshot.summary || "").slice(0, 140))}…</div>` : '<div class="pp-meta">画像未合成</div>'}
        <div class="pp-r-rep">代表作：${r.representative.slice(0, 2).map(p => `<a class="pp-ext" href="${p.pmid ? "https://pubmed.ncbi.nlm.nih.gov/" + p.pmid + "/" : "#"}" target="_blank">${esc((p.title || "").slice(0, 34))}</a>`).join("，") || "无"}</div>
        <div class="pp-r-foot">
          <span class="pp-meta">论文 ${r.papers}</span>
          ${r.contact?.orcid ? `<a class="pp-ext" href="${esc(r.contact.orcid)}" target="_blank">ORCID</a>` : ""}
          <button class="pp-btn pp-btn-ghost pp-btn-sm" data-open="${esc(r.id)}">档案</button>
        </div>
      </div>`;
    grid.innerHTML = d.researchers.map(card).join("") || '<div class="pp-muted">该方向暂无研究者</div>';
    grid.querySelectorAll("[data-open]").forEach(b => b.addEventListener("click", () =>
      this.openAuthor((b as HTMLElement).dataset.open!)));
    grid.querySelectorAll(".pp-r-card").forEach(c => c.addEventListener("click", e => {
      if ((e.target as HTMLElement).closest("a")) return;
      this.openAuthor((c as HTMLElement).dataset.aid!);
    }));
  }

  // ---------- 热点时间线 ----------
  async loadTrends() {
    const sec = this.el.querySelector('section[data-sec="trends"]') as HTMLElement;
    this.trends = await this.provider.trends(this.domain);
    const top = this.trends.series.filter(s => s.display !== "excluded").slice(0, 10);
    sec.innerHTML = `<div class="pp-card">
        <div class="pp-card-title">方向 × 年份 论文热度<span class="pp-meta">（主要方向，堆叠面积）</span></div>
        <div class="pp-chart" id="pp-trendchart" style="height:380px"></div></div>
      <div class="pp-card">
        <div class="pp-card-title">近三年活跃方向榜</div>
        <table class="pp-table"><tr><th>方向</th><th>近三年论文</th><th>前三年</th><th>增速</th></tr>
        ${top.map(s => `<tr><td>${dirName(s)}</td><td>${s.recent}</td><td>${s.prev}</td>
          <td>${s.growth > 0.05 ? `<span class="pp-up">▲ ${Math.round(s.growth * 100)}%</span>` : s.growth < -0.05 ? `<span class="pp-down">▼ ${Math.round(Math.abs(s.growth) * 100)}%</span>` : "—"}</td></tr>`).join("")}
        </table></div>`;
    const years = Array.from(new Set(top.flatMap(s => Object.keys(s.years)))).sort();
    const chart = echarts.init(sec.querySelector("#pp-trendchart") as HTMLElement);
    this.charts.push(chart);
    chart.setOption({
      tooltip: { trigger: "axis" },
      legend: { type: "scroll", bottom: 0, textStyle: { fontSize: 10 } },
      grid: { left: 40, right: 20, top: 30, bottom: 40 },
      xAxis: { type: "category", data: years },
      yAxis: { type: "value" },
      series: top.map((s, i) => ({
        name: s.name || `方向#${s.label}`, type: "line", stack: "t",
        smooth: true, showSymbol: false,
        lineStyle: { width: 0 }, areaStyle: { opacity: 0.75 },
        itemStyle: { color: PALETTE[s.label % PALETTE.length] },
        emphasis: { focus: "series" },
        data: years.map(y => s.years[y] ?? 0),
      })),
    });
  }

  // ---------- 研究者档案 ----------
  /** 研究者档案（笔记式）：画像 / 标签 / 代表作 / 履历 / 编辑 */
  async openAuthor(id: string) {
    this.showTab("author");
    const sec = this.el.querySelector('section[data-sec="author"]') as HTMLElement;
    sec.innerHTML = '<div class="pp-muted">加载中…</div>';
    const [a, snap, tags] = await Promise.all([
      this.provider.author(id),
      this.provider.authorSnapshot(id).catch(() => null),
      this.provider.authorTags(id).catch(() => []),
    ]);
    const f0 = a.flags.filter((f: any) => f.level === "L0" && f.status !== "dismissed");
    const f1 = a.flags.filter((f: any) => f.level === "L1" && f.status !== "dismissed");
    const dirNameOf = (cid: number) => {
      const d = this.dirs?.directions.find((x: any) => x.cluster_id === cid);
      return d?.name || `方向 #${cid}`;
    };
    const flagBadge = (f: any) => f.level === "L0"
      ? '<span class="pp-badge pp-b-l0">⛔ L0 确认造假</span>'
      : '<span class="pp-badge pp-b-l1">⚠ L1 风险提示</span>';
    const tagsHtml = (tags || []).map((x: any) =>
      `<span class="pp-chip${x.status === "pending" ? " pp-chip-pending" : ""}" title="${esc(x.dim)}${x.status === "pending" ? "（待审）" : ""}">${esc(x.tag)}</span>`).join("");
    const note = a.note ?? "";
    sec.innerHTML = `
    <div class="pp-row" style="align-items:flex-start">
      <div class="pp-grow">
        <div class="pp-card">
          <div class="pp-panel-head"><b>研究者档案</b>
            <button class="pp-btn pp-btn-sm pp-btn-ghost" data-vault-edit>✎ 打开笔记编辑</button></div>
          <h2 style="margin:2px 0 4px">${esc(a.name_display)}
            ${a.name_zh ? `<span class="pp-meta">（${esc(a.name_zh)}）</span>` : ""}
            <span class="pp-badge ${a.tier === "core" ? "pp-b-core" : "pp-b-peripheral"}">${a.tier === "core" ? "核心层" : "外围层"}</span>
            ${f0.map(flagBadge).join("")}${f1.map(flagBadge).join("")}</h2>
          <div class="pp-meta">${esc(a.id)} · 域内论文 <b>${a.papers?.length ?? 0}</b>
            ${a.affiliations?.[0]?.institution ? ` · 🏛 ${esc(a.affiliations[0].institution)}${a.affiliations[0].source_tag === "web" && a.affiliations[0].verified !== 1 ? ' <span class="pp-badge pp-b-pending">官网待校验</span>' : ""}` : ""}
            ${a.orcid ? ` · <a class="pp-ext" href="${esc(a.orcid)}" target="_blank">ORCID</a>` : ""}</div>
          ${(a.clusters?.length || tagsHtml) ? `<div class="pp-chips" style="margin-top:8px">
            ${(a.clusters || []).map((c: any) => `<span class="pp-chip pp-chip-dir" data-dir="${c.id}">◈ ${esc(dirNameOf(c.id))}</span>`).join("")}
            ${tagsHtml}</div>` : ""}
          ${note ? `<div class="pp-sec"><b>📝 笔记</b><div class="pp-meta">${esc(note)}</div></div>` : ""}
        </div>
        ${snap?.content ? `
        <div class="pp-card pp-snapcard">
          <div class="pp-card-title">画像 ${statusBadge(snap.review_status || "pending")}
            <span class="pp-meta">${esc((snap as any).model || "")}</span></div>
          ${snap.content.focus ? `<div class="pp-snap-focus">🎯 ${esc(snap.content.focus)}</div>` : ""}
          ${snap.content.summary ? `<div class="pp-meta">${esc(snap.content.summary)}</div>` : ""}
          ${snap.content.key_contributions ? `<div class="pp-sec"><b>主要贡献</b><div class="pp-meta">${esc(Array.isArray(snap.content.key_contributions) ? snap.content.key_contributions.join("；") : snap.content.key_contributions)}</div></div>` : ""}
          ${snap.content.risks ? `<div class="pp-sec"><b>风险</b><div class="pp-meta">${esc(snap.content.risks)}</div></div>` : ""}
        </div>` : '<div class="pp-card pp-meta">该研究者画像未生成（skill 会话合成后出现）</div>'}
        <div class="pp-card"><div class="pp-card-title">代表作（点击查看摘要/编辑）</div>
          <div class="pp-r-rep">${a.papers.slice(0, 5).map((p: any) => `
            <div class="pp-ev"><a href="javascript:;" data-paper="${p.id}">${esc(p.title)}</a>
            <span class="pp-meta">（${p.year || ""} · 被引 ${p.cited_by_count || 0}）</span></div>`).join("") || "无"}</div>
          <details><summary class="pp-meta">全部论文（${a.papers.length}）</summary>
            <table class="pp-table"><tr><th>年份</th><th>标题</th><th>被引</th><th>状态</th></tr>
            ${a.papers.map((p: any) => `<tr><td>${p.year || ""}</td>
              <td><a href="javascript:;" data-paper="${p.id}">${esc(p.title)}</a></td>
              <td>${p.cited_by_count || 0}</td><td>${flagBadges(p)}</td></tr>`).join("")}</table></details>
        </div>
        ${this.live ? `
        <div class="pp-card"><div class="pp-card-title">编辑（写回数据库）</div>
          <div class="pp-row" style="gap:8px">
            <input id="pp-namezh" class="pp-input" placeholder="汉字真名" value="${esc(a.name_zh || "")}" style="flex:1">
            <button class="pp-btn" data-act="hanzi">存汉字名</button></div>
          <textarea id="pp-author-note" class="pp-input" style="width:100%;margin-top:6px" rows="2">${esc(note)}</textarea>
          <button class="pp-btn pp-btn-primary" style="margin-top:6px" data-act="note">保存笔记</button>
        </div>` : `<div class="pp-meta">启用「实时服务」后可在插件内编辑（汉字名/笔记）；或直接编辑 vault 笔记（manual_*）。</div>`}
      </div>
      <div class="pp-side">
        ${a.affiliations?.length ? `<div class="pp-card"><div class="pp-card-title">时间履历</div><div class="pp-timeline">
          ${a.affiliations.map((x: any) => `<div class="pp-ev"><span class="pp-ev-y">${x.start_year || "?"}${x.end_year && x.end_year !== x.start_year ? "–" + x.end_year : ""}</span>
            ${esc(x.institution)} <span class="pp-meta">[${x.source_tag}]${x.source_tag === "web" && x.verified !== 1 ? " 待校验" : ""}</span></div>`).join("")}
          </div></div>` : ""}
        ${a.collaborators?.length ? `<div class="pp-card"><div class="pp-card-title">合作者（前 10）</div>
          <div class="pp-chips">${a.collaborators.slice(0, 10).map((c: any) =>
            `<span class="pp-chip" data-open="${esc(c.id)}">${esc(c.name_display)} ×${c.co_papers}</span>`).join("")}</div></div>` : ""}
      </div>
    </div>`;
    sec.querySelectorAll("[data-paper]").forEach((b) => b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this.openPaper((b as HTMLElement).dataset.paper!);
    }));
    sec.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () =>
      this.openAuthor((b as HTMLElement).dataset.open!)));
    sec.querySelectorAll("[data-dir]").forEach((b) => b.addEventListener("click", () =>
      this.openDirection(+(b as HTMLElement).dataset.dir!)));
    const ve = sec.querySelector("[data-vault-edit]");
    ve?.addEventListener("click", async () => {
      const f = this.plugin.app.vault.getFiles().find((x) => x.path.endsWith(`/researchers/${id}.md`));
      if (f) {
        const leaf = this.plugin.app.workspace.getLeaf(true);
        await leaf.openFile(f);
        await leaf.setViewState({ type: "markdown", state: { file: f.path, mode: "source" }, active: true });
      } else new Notice("该研究者笔记未在 vault");
    });
    sec.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
      const act = (b as HTMLElement).dataset.act!;
      if (!this.live) { new Notice("需启用实时服务"); return; }
      if (act === "hanzi") {
        const v = (sec.querySelector("#pp-namezh") as HTMLInputElement).value.trim();
        if (v) { await this.live.post(`/api/author/${id}/hanzi`, { hanzi: v, by: this.user }); this.openAuthor(id); }
      } else if (act === "note") {
        await this.live.post(`/api/author/${id}/note`, { note: (sec.querySelector("#pp-author-note") as HTMLTextAreaElement).value, by: this.user });
        new Notice("笔记已保存");
        this.openAuthor(id);
      }
    }));
  }

  // ---------- 造假事件（卡片 + overlay 详情，渲染 vault 事件笔记） ----------
  async loadEvents() {
    const sec = this.el.querySelector('section[data-sec="events"]') as HTMLElement;
    const evs = await this.provider.events();
    sec.innerHTML = `<div class="pp-card"><div class="pp-card-title">造假事件
      <span class="pp-meta">事件以 vault 笔记呈现 · 点卡片查看详情/标记操作</span></div>
      <div class="pp-res-grid">
      ${evs.length ? evs.map(e => `
        <div class="pp-card pp-r-card" data-ev="${e.id}">
          <div class="pp-r-name">${esc(e.title)} ${statusBadge(e.status)}</div>
          <div class="pp-meta">${esc(e.slug)}</div>
          <div class="pp-meta" style="margin-top:6px">论文标记 <b>${e.n_paper_flags}</b> ·
            L0 <b>${e.n_l0}</b> · L1 <b>${e.n_l1}</b></div>
        </div>`).join("") : '<div class="pp-meta">该域暂无造假事件</div>'}
      </div></div>`;
    sec.querySelectorAll("[data-ev]").forEach(b => b.addEventListener("click", () =>
      this.openEvent(+(b as HTMLElement).dataset.ev!)));
  }

  /** 打开事件 overlay：渲染 vault 事件笔记（MarkdownRenderer）+ Live 标记操作 */
  async openEvent(id: number) {
    const ev = (await this.provider.events()).find(x => x.id === id);
    if (!ev) return;
    const overlay = (this.el.querySelector(".pp-main") as HTMLElement).createEl("div", { cls: "pp-overlay" });
    const card = overlay.createEl("div", { cls: "pp-card pp-paper-panel" });
    card.innerHTML = `<div class="pp-panel-head"><b>造假事件</b>
      <button class="pp-btn pp-btn-ghost pp-btn-sm" data-close>✕</button></div>`;
    const body = card.createDiv();
    // vault 事件笔记渲染（若有）
    const f = this.plugin.app.vault.getFiles().find(x => x.path.endsWith(`/events/event-${id}.md`));
    let mdText = `# ${esc(ev.title)}  `;
    if (f) {
      mdText = await this.plugin.app.vault.adapter.read(f.path);
    } else if (ev.status) {
      mdText = `# ${ev.title}\n\n> 状态：${ev.status}\n\n来源：${(ev.source_urls||[]).join(", ")}`;
    }
    const cm = new Component();
    await MarkdownRenderer.render(this.plugin.app, mdText, body, "", cm);
    // Live：人员标记操作
    if (this.live) {
      const detail = await this.live.get(`/api/event/${id}`);
      const flags = detail.author_flags || [];
      if (flags.length) {
        const fl = body.createDiv({ cls: "pp-sec" });
        fl.innerHTML = `<b>人员标记（实时数据）</b>`;
        for (const x of flags) {
          const row = fl.createDiv({ cls: "pp-meta", attr: { style: "margin:4px 0" } });
          row.innerHTML = `[[${esc(x.name_display)}]] <span class="pp-badge pp-b-${x.level.toLowerCase()}">${x.level}</span> ${statusBadge(x.status)} ${esc((x.basis || "").slice(0, 80))}`;
          if (x.level === "L0" && x.status === "pending") {
            const inp = row.createEl("input", { cls: "pp-input pp-input-sm", attr: { placeholder: "定性依据" } });
            const btn = row.createEl("button", { cls: "pp-btn pp-btn-sm pp-btn-danger", text: "人工确认 L0" });
            btn.addEventListener("click", async () => {
              if (!confirm("确认执行 L0 定性？")) return;
              await this.live!.post(`/api/flag/${x.id}/confirm-l0`, { by: this.user, basis: inp.value || "" });
              overlay.remove(); this.loadEvents();
            });
          } else if (x.level === "L1" && x.status !== "dismissed") {
            const b2 = row.createEl("button", { cls: "pp-btn pp-btn-sm pp-btn-ghost", text: "排除" });
            b2.addEventListener("click", async () => {
              await this.live!.post(`/api/flag/${x.id}/dismiss`, { by: this.user });
              overlay.remove(); this.loadEvents();
            });
          }
          row.insertBefore(document.createTextNode(" "), row.lastChild);
        }
      }
    }
    overlay.querySelector("[data-close]")?.addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", (ev2: MouseEvent) => { if (ev2.target === overlay) overlay.remove(); });
  }

  // ---------- 检索 ----------
  setupSearch() {
    const input = this.el.querySelector(".pp-q") as HTMLInputElement;
    const res = this.el.querySelector(".pp-qres") as HTMLElement;
    let t: number | null = null;
    input.addEventListener("input", () => {
      if (t) clearTimeout(t);
      t = window.setTimeout(async () => {
        const v = input.value.trim();
        if (!v) { res.style.display = "none"; return; }
        const r = await this.provider.search(v);
        res.innerHTML = r.authors.map((a: any) =>
          `<div data-open="${esc(a.id)}">👤 ${esc(a.name_display)}${a.name_zh ? "（" + esc(a.name_zh) + "）" : ""}
           <span class="pp-meta">${a.papers} 篇${a.l0 ? " ⛔L0" : ""}${a.l1 ? " ⚠L1" : ""}</span></div>`).join("") +
          r.papers.map((p: any) =>
            `<div data-pmid="${p.pmid || ""}">📄 ${esc((p.title || "").slice(0, 50))} <span class="pp-meta">${p.year || ""}</span></div>`).join("") ||
          '<div class="pp-meta">无结果</div>';
        res.style.display = "block";
        res.querySelectorAll("[data-open]").forEach(x => x.addEventListener("click", () => {
          this.openAuthor((x as HTMLElement).dataset.open!);
          res.style.display = "none";
        }));
        res.querySelectorAll("[data-pmid]").forEach(x => x.addEventListener("click", () => {
          const pmid = (x as HTMLElement).dataset.pmid!;
          if (pmid) window.open("https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/");
          res.style.display = "none";
        }));
      }, 250);
    });
    document.addEventListener("click", e => {
      if (!(e.target as HTMLElement).closest(".pp-searchwrap")) res.style.display = "none";
    });
  }
}

/** 常规视图（ribbon/命令打开，默认域） */
export class AtlasView extends ItemView {
  plugin: PeoparPlugin;
  private core: AtlasApp | null = null;
  constructor(leaf: any, plugin: PeoparPlugin) {
    super(leaf);
    this.plugin = plugin;
  }
  getViewType(): string { return VIEW_TYPE; }
  getDisplayText(): string { return "百官行述 · 研究者图谱"; }
  getIcon(): string { return "network"; }
  async onOpen() {
    this.core = new AtlasApp(this.plugin, this.contentEl);
    await this.core.mount();
  }
  async onClose() { this.core?.dispose(); this.core = null; }
}

/** 文件视图：双击 .peopar 文件打开（frontmatter.domain 指定域） */
export class PeoparFileView extends FileView {
  plugin: PeoparPlugin;
  private core: AtlasApp | null = null;
  constructor(leaf: any, plugin: PeoparPlugin) {
    super(leaf);
    this.plugin = plugin;
  }
  getViewType(): string { return FILE_VIEW_TYPE; }
  getDisplayText(): string { return this.file ? this.file.basename : "百官行述"; }
  getIcon(): string { return "network"; }

  async onOpen() { await this.render(); }
  async onLoadFile(file: TFile) { await this.render(); }
  async onUnloadFile(file: TFile) { this.core?.dispose(); this.core = null; this.contentEl.empty(); }

  private async render() {
    if (!this.file) return;
    const fm = this.app.metadataCache.getFileCache(this.file)?.frontmatter;
    const domain = fm?.domain ?? "neuroling";
    this.contentEl.empty();
    this.core?.dispose();
    this.core = new AtlasApp(this.plugin, this.contentEl);
    await this.core.mount(domain);
  }
}
