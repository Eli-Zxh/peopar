/** VaultProvider：从 vault 内 peopar/ 目录的 md 快照读取数据（零服务器）。
 *  把 frontmatter 组装成与 LiveProvider 相同的 API 兼容结构，视图层无感知。 */
import { App, TFile } from "obsidian";
import {
  DataProvider, Domain, DirectionsResp, TrendsResp, DirectionResp, Researcher,
  AuthorDetail, AuthorSnapshotResp, FraudEvent, FraudEventDetail, Inst, LayoutData,
} from "./api";

export class VaultProvider implements DataProvider {
  private P: string;
  constructor(private app: App, topic: string = "神经语言学与失语症") {
    this.P = topic ? `${topic}/peopar` : "peopar";
  }

  serverConnected(): boolean { return false; }
  writeSupported(): boolean { return false; }

  lastSync(): string | null {
    const f = this.file(`${this.P}/_sync.md`);
    return f ? (this.fm(f).synced_at ?? null) : null;
  }

  private file(path: string): TFile | null {
    return this.app.vault.getFiles().find(f => f.path === path) ?? null;
  }
  private files(sub: string): TFile[] {
    return this.app.vault.getFiles().filter(f => f.path.startsWith(`${this.P}/${sub}/`) && f.extension === "md");
  }
  private fm(f: TFile): any {
    return this.app.metadataCache.getFileCache(f)?.frontmatter ?? {};
  }
  private parseJson(v: any): any {
    if (v == null) return null;
    if (typeof v === "object") return v;
    try { return JSON.parse(String(v)); } catch { return null; }
  }

  async domains(): Promise<Domain[]> {
    const sync = this.file(`${this.P}/_sync.md`);
    const stats = sync ? (this.parseJson(this.fm(sync).domains) ?? {}) : {};
    const out: Domain[] = [];
    for (const f of this.app.vault.getFiles()) {
      const m = /^.+\/peopar\/([a-z0-9_]+)\.peopar$/.exec(f.path);
      if (!m) continue;
      const fm = this.fm(f);
      const id = fm.domain ?? m[1];
      const st = stats[id] ?? {};
      out.push({ id, name: id, papers: st.papers ?? 0, authors: st.researchers ?? 0, graph_ready: true });
    }
    // 兜底：_sync.md domains 键
    if (!out.length) {
      for (const [id, st] of Object.entries(stats)) {
        out.push({ id, name: id, papers: (st as any).papers ?? 0, authors: (st as any).researchers ?? 0, graph_ready: true });
      }
    }
    return out;
  }

  async directions(domain: string): Promise<DirectionsResp> {
    const ds: any[] = [];
    for (const f of this.files("directions")) {
      const fm = this.fm(f);
      if (fm.domain !== domain || fm.type !== "direction") continue;
      ds.push({
        cluster_id: fm.direction_id, label: fm.label ?? 0, name: fm.name ?? null,
        display: "normal", size: fm.size ?? 0, papers: fm.papers ?? 0,
        recent: fm.recent ?? 0, citations: fm.citations ?? 0,
        snap_review: fm.review === "none" ? null : fm.review,
        top_authors: (fm.top_authors || []).slice(0, 5).map((n: string) => ({ id: "", name: n, tier: "", papers: 0 })),
        _years: this.parseJson(fm.years) ?? {},
        _linked: fm.linked ?? [],
      });
    }
    ds.sort((a, b) => b.size - a.size);
    const links: any[] = [];
    const seen = new Set<string>();
    for (const d of ds) {
      for (const cid of d._linked ?? []) {
        const k = Math.min(d.cluster_id, cid) + "-" + Math.max(d.cluster_id, cid);
        if (seen.has(k)) continue;
        seen.add(k);
        links.push({ source: d.cluster_id, target: cid, shared_papers: 1 });
      }
    }
    return { domain, directions: ds, links };
  }

  async trends(domain: string): Promise<TrendsResp> {
    const series: any[] = [];
    for (const f of this.files("directions")) {
      const fm = this.fm(f);
      if (fm.domain !== domain || fm.type !== "direction") continue;
      const years = this.parseJson(fm.years) ?? {};
      const entries = Object.entries(years).map(([y, n]) => [+y, n as number]);
      const recent = entries.filter(([y]) => y >= 2024).reduce((s, [, n]) => s + n, 0);
      const prev = entries.filter(([y]) => 2021 <= y && y < 2024).reduce((s, [, n]) => s + n, 0);
      series.push({
        cluster_id: fm.direction_id, label: fm.label ?? 0, name: fm.name ?? null,
        display: "normal", years: years as Record<string, number>,
        recent, prev, growth: prev ? (recent - prev) / prev : (recent ? 1 : 0),
      });
    }
    series.sort((a, b) => b.recent - a.recent);
    return { domain, series: series.slice(0, 20) };
  }

  async layout(domain: string): Promise<LayoutData> {
    const f = this.file(`${this.P}/_layout/${domain}.json`);
    if (!f) throw new Error("无布局快照（先运行 analyze/layout.py 并 export_vault）");
    const raw = await this.app.vault.adapter.read(f.path);
    return JSON.parse(raw);
  }

  async directionResearchers(cid: number): Promise<DirectionResp> {
    const dfile = this.file(`${this.P}/directions/direction-${cid}.md`);
    const dfm = dfile ? this.fm(dfile) : {};
    const researchers: Researcher[] = [];
    for (const f of this.files("researchers")) {
      const fm = this.fm(f);
      if (fm.type !== "researcher" || !(fm.directions || []).includes(cid)) continue;
      researchers.push(this.toResearcher(fm));
    }
    researchers.sort((a, b) => b.papers - a.papers);
    return { cluster_id: cid, name: dfm.name ?? null, label: dfm.label ?? 0, display: "normal", domain: dfm.domain ?? "", researchers };
  }

  private toResearcher(fm: any): Researcher {
    const reps = (fm.representative || []).map((pid: number) => {
      const pf = this.file(`${this.P}/papers/paper-${pid}.md`);
      const pfm = pf ? this.fm(pf) : {};
      return { id: pid, title: pfm.title ?? "", year: pfm.year ?? undefined, pmid: pfm.pmid || undefined, cited_by_count: pfm.cited ?? 0 };
    });
    return {
      id: fm.id, name: fm.name ?? "", zh: fm.name_zh || null, tier: fm.tier ?? "peripheral",
      papers: fm.papers ?? 0,
      institution: fm.institution ? {
        institution: fm.institution, start_year: null, end_year: null,
        source_tag: fm.inst_verified === 1 ? "web" : "auto", verified: fm.inst_verified ?? 0,
      } : null,
      snapshot: fm.focus ? {
        focus: fm.focus, summary: fm.summary ?? "", key_contributions: "",
        risks: "", review_status: fm.review ?? "pending", id: undefined,
      } : null,
      representative: reps.slice(0, 3),
      contact: { orcid: fm.orcid || null, openalex_id: null },
    };
  }

  async author(id: string): Promise<AuthorDetail> {
    const f = this.file(`${this.P}/researchers/${id}.md`);
    const fm = f ? this.fm(f) : {};
    // 该作者论文：解析 vault 研究者笔记正文「## 论文」清单行 + frontmatter paper_ids
    const papers: any[] = [];
    const pids: number[] = (fm.paper_ids || []);
    if (f && pids.length) {
      const txt = await this.app.vault.adapter.read(f.path);
      const listRe = /^\d+\.\s+(.+?)\s*（(\d{4})\s*·\s*被引\s*(\d+)）(?:\s*\[PubMed\]\(https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/(\d+)\/\))?/gm;
      let m: RegExpExecArray | null; let i = 0;
      while ((m = listRe.exec(txt)) !== null && i < pids.length) {
        const pid = pids[i++];
        const title = m[1].replace(/\u005C[\u005C?]/g, "").replace(/\*\*/g, "");
        papers.push({ id: pid, title, year: +m[2], cited_by_count: +m[3],
          pmid: m[4] || null, journal: "", retraction_status: "none", doi: null,
          position: null, n_flags: 0 });
      }
    }
    // 论文正文无清单时尝试布局 JSON（图节点论文）兜底
    if (!papers.length) {
      const lay = await this.layout("neuroling").catch(() => null);
      if (lay) {
        for (const pn of lay.papers || []) {
          const pid = Number(pn.id.replace("p:", ""));
          if (pids.includes(pid)) {
            papers.push({ id: pid, title: pn.title || "", year: null, cited_by_count: pn.cite ?? 0,
              pmid: pn.pmid || null, journal: "", retraction_status: "none", doi: null,
              position: null, n_flags: 0 });
          }
        }
      }
    }
    const flags: any[] = [];
    const dirs: any[] = [];
    for (const pf of this.files("papers")) {
      const pfm = this.fm(pf);
      if ((pfm.authors || []).includes(id)) {
        papers.push({
          id: pfm.paper_id, title: pfm.title ?? "", year: pfm.year ?? null,
          journal: pfm.journal ?? "", pmid: pfm.pmid || null, doi: pfm.doi || null,
          cited_by_count: pfm.cited ?? 0, retraction_status: pfm.retraction ?? "none",
          position: null, n_flags: 0,
        });
        if ((pfm.retraction ?? "none") !== "none") flags.push({ paper_id: pfm.paper_id, level: "L1", status: "confirmed" });
      }
    }
    papers.sort((a, b) => (b.cited_by_count ?? 0) - (a.cited_by_count ?? 0));
    for (const lvl of (fm.flags || [])) {
      flags.push({ level: lvl, status: "confirmed", basis: "vault 快照", event_title: "标记（源自快照）" });
    }
    for (const cid of (fm.directions || [])) {
      const df = this.file(`${this.P}/directions/direction-${cid}.md`);
      const dfm = df ? this.fm(df) : {};
      dirs.push({ id: cid, label: dfm.label ?? 0, name: dfm.name ?? null, batch_id: "vault", weight: 0 });
    }
    const affs: Inst[] = fm.institution ? [{
      institution: fm.institution, start_year: null, end_year: null,
      source_tag: fm.inst_verified === 1 ? "web" : "auto", verified: fm.inst_verified ?? 0,
    }] : [];
    return {
      id, name_display: fm.name ?? "", name_zh: fm.name_zh || null, tier: fm.tier ?? "peripheral",
      orcid: fm.orcid || null, openalex_id: null,
      aliases: [], affiliations: affs, flags, clusters: dirs, papers, collaborators: [], audit: [],
      note: fm.manual_note ?? null,
    };
  }

  async authorTags(id: string): Promise<{ tag: string; dim: string; status: string }[]> { return []; }

  async authorSnapshot(id: string): Promise<AuthorSnapshotResp | null> {
    const f = this.file(`${this.P}/researchers/${id}.md`);
    const fm = f ? this.fm(f) : {};
    if (!fm.focus && !fm.review) return null;
    return {
      id: 0, review_status: fm.review ?? "pending", generated_at: this.lastSync() ?? undefined,
      model: "vault 快照", content: {
        focus: fm.focus ?? "", summary: fm.summary ?? "", key_contributions: "",
        risks: "", review_status: fm.review ?? "pending",
      },
      evidence: (fm.representative || []).map((pid: number) => {
        const pf = this.file(`${this.P}/papers/paper-${pid}.md`);
        const pfm = pf ? this.fm(pf) : {};
        return { role: "representative", paper_id: pid, title: pfm.title ?? "", year: pfm.year, journal: pfm.journal, pmid: pfm.pmid, doi: pfm.doi, cited_by_count: pfm.cited ?? 0, retraction_status: pfm.retraction ?? "none" };
      }),
    };
  }

  async events(): Promise<FraudEvent[]> {
    const out: FraudEvent[] = [];
    for (const f of this.files("events")) {
      const fm = this.fm(f);
      out.push({
        id: fm.event_id, slug: fm.slug ?? "", title: fm.title ?? "", description: "",
        status: fm.status ?? "suspected", source_urls: fm.source_urls ?? [],
        n_paper_flags: (fm.paper_flags || []).length,
        n_l0: (fm.author_flags || []).filter((x: any) => x.level === "L0").length,
        n_l1: (fm.author_flags || []).filter((x: any) => x.level === "L1").length,
      });
    }
    return out;
  }

  async eventDetail(id: number): Promise<FraudEventDetail> {
    const f = this.file(`${this.P}/events/event-${id}.md`);
    const fm = f ? this.fm(f) : {};
    const paperFlags = (fm.paper_flags || []).map((x: any) => ({
      id: 0, paper_id: x.paper_id, event_id: id, flag_type: x.flag_type, note: "", source_url: null,
      created_by: "vault", created_at: "", title: x.title ?? "", year: null, pmid: null,
    }));
    const authorFlags = (fm.author_flags || []).map((x: any) => ({
      id: 0, author_id: x.author_id, event_id: id, level: x.level, basis: x.basis ?? "",
      status: x.status ?? "pending", confirmed_by: null, created_by: "vault", created_at: "",
      name_display: this.resName(x.author_id), name_zh: null, tier: "peripheral",
    }));
    return {
      id, slug: fm.slug ?? "", title: fm.title ?? "", description: "", status: fm.status ?? "suspected",
      source_urls: fm.source_urls ?? [], n_paper_flags: paperFlags.length,
      n_l0: authorFlags.filter(x => x.level === "L0").length,
      n_l1: authorFlags.filter(x => x.level === "L1").length,
      paper_flags: paperFlags, author_flags: authorFlags, audit: [],
    };
  }

  private resName(id: string): string {
    const f = this.file(`${this.P}/researchers/${id}.md`);
    return f ? (this.fm(f).name ?? id) : id;
  }

  async search(q: string): Promise<any> {
    const ql = q.toLowerCase();
    const authors: any[] = [];
    const papers: any[] = [];
    for (const f of this.files("researchers")) {
      const fm = this.fm(f);
      if (!fm.name) continue;
      if (String(fm.name).toLowerCase().includes(ql) || String(fm.name_zh ?? "").includes(ql)) {
        authors.push({ id: fm.id, name_display: fm.name, name_zh: fm.name_zh || null, papers: fm.papers ?? 0, l0: (fm.flags || []).includes("L0"), l1: (fm.flags || []).includes("L1") });
      }
      if (authors.length >= 30) break;
    }
    for (const f of this.files("papers")) {
      const fm = this.fm(f);
      if (fm.title && String(fm.title).toLowerCase().includes(ql)) {
        papers.push({ id: fm.paper_id, title: fm.title, year: fm.year, journal: fm.journal, pmid: fm.pmid, retraction_status: fm.retraction ?? "none" });
      }
      if (papers.length >= 30) break;
    }
    return { authors, papers };
  }
}
