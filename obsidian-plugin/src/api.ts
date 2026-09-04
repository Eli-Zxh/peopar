/** peopar 本地服务 API 客户端（127.0.0.1:8765）。 */

export const API_BASE = "http://127.0.0.1:8765";

export async function api<T = any>(path: string, opt?: RequestInit): Promise<T> {
  const r = await fetch(API_BASE + path, opt);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const j = await r.json();
      if (j?.error) msg = j.error;
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export async function ping(): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch(API_BASE + "/api/health", { signal: ctrl.signal });
    clearTimeout(t);
    return r.ok;
  } catch {
    return false;
  }
}

export interface Domain { id: string; name: string; papers: number; authors: number; graph_ready: boolean; }
export interface TopAuthor { id: string; name: string; zh?: string | null; tier: string; papers: number; }
export interface Direction {
  cluster_id: number; label: number; name: string | null; display: string;
  size: number; papers: number; recent: number; citations: number;
  snap_review?: string | null; top_authors: TopAuthor[];
}
export interface DirectionsResp { domain: string; directions: Direction[]; links: { source: number; target: number; shared_papers: number }[]; }

export interface TrendSeries { cluster_id: number; label: number; name: string | null; display: string; years: Record<string, number>; recent: number; prev: number; growth: number; }
export interface TrendsResp { domain: string; series: TrendSeries[]; }

export interface Inst { institution: string; start_year?: number | null; end_year?: number | null; source_tag: string; verified: number; note?: string | null; }
export interface AuthorSnap { id?: number; focus?: string; summary?: string; key_contributions?: string; risks?: string; review_status?: string; generated_at?: string; model?: string; }
export interface Researcher {
  id: string; name: string; zh?: string | null; tier: string; papers: number;
  institution: Inst | null; snapshot: AuthorSnap | null;
  representative: { id: number; title: string; year?: number; pmid?: string; cited_by_count?: number }[];
  contact: { orcid?: string | null; openalex_id?: string | null };
}
export interface DirectionResp { cluster_id: number; name: string | null; label: number; display: string; domain: string; researchers: Researcher[]; }

export interface AuthorDetail {
  id: string; name_display: string; note?: string | null; name_zh?: string | null; tier: string; orcid?: string | null; openalex_id?: string | null;
  aliases: any[]; affiliations: Inst[]; flags: any[]; clusters: any[]; papers: any[]; collaborators: any[];
  audit: any[];
}
export interface AuthorSnapshotResp { id?: number; content?: AuthorSnap; review_status?: string; generated_at?: string; model?: string; evidence?: any[]; name_display?: string; }

export interface FraudEvent { id: number; slug: string; title: string; description?: string; status: string; source_urls: string[]; n_paper_flags: number; n_l0: number; n_l1: number; }
export interface FraudEventDetail extends FraudEvent { paper_flags: any[]; author_flags: any[]; audit: any[]; report?: any; }

export interface Judgment { id: number; jtype: string; entity_type: string; entity_id: string; proposal: any; status: string; created_at: string; decided_by?: string; }
export interface WebvpnImport { id: number; domain_id: string; source: string; file_name?: string; query?: string; n_records: number; n_new: number; n_dup: number; imported_at: string; }

export interface SnapshotQueueItem { id: number; kind?: string; domain_id?: string; cluster_id?: number; author_id?: string; author_name?: string; content: any; model?: string; review_status: string; n_evidence: number; generated_at: string; }

/** 数据提供者：LiveProvider（本地服务 API）与 VaultProvider（vault md 快照）实现同一接口，
 *  视图层不感知数据来源。静态（vault md）模式零服务器可用。 */
export interface DataProvider {
  serverConnected(): boolean;
  lastSync(): string | null;              // _sync.md 时间戳（静态模式）
  domains(): Promise<Domain[]>;
  directions(domain: string): Promise<DirectionsResp>;
  trends(domain: string): Promise<TrendsResp>;
  layout(domain: string): Promise<LayoutData>;
  directionResearchers(cid: number): Promise<DirectionResp>;
  author(id: string): Promise<AuthorDetail>;
  authorSnapshot(id: string): Promise<AuthorSnapshotResp | null>;
  authorTags(id: string): Promise<{ tag: string; dim: string; status: string }[]>;
  events(): Promise<FraudEvent[]>;
  eventDetail(id: number): Promise<FraudEventDetail>;
  search(q: string): Promise<any>;
  /** 仅 LiveProvider 支持（写操作/管理台）；静态模式返回 false */
  writeSupported(): boolean;
}

/** 信息化方向图谱布局数据（analyze/layout.py 产物；宏观区域 + 论文/作者散点 + 连线） */
export interface LayoutNode { id: string; x: number; y: number; r: number; cluster_id: number; affinity: number | null; }
export interface LayoutDir extends LayoutNode { name: string | null; size: number; }
export interface LayoutPaper extends LayoutNode { paper_id?: number; title?: string; cite?: number; abstract?: string; note?: string; pmid?: string | null; }
export interface LayoutAuthor extends LayoutNode { name?: string; zh?: string; }
export interface LayoutData {
  domain: string; batch: string;
  directions: LayoutDir[]; papers: LayoutPaper[]; authors: LayoutAuthor[];
  edges: { source: string; target: string; kind: string }[];
}
