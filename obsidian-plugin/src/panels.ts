/** 面板渲染：研究者档案 / 造假事件 / 管理台（返回 HTML 字符串）。 */
import { api, AuthorDetail, AuthorSnapshotResp, FraudEvent, FraudEventDetail, Judgment, SnapshotQueueItem, WebvpnImport } from "./api";
import { esc, statusBadge, flagBadges, dirName } from "./views";

const pmidLink = (p: any) => p?.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/` : "#";

// ---------- 研究者档案 ----------
export function renderAuthor(a: AuthorDetail, snap: AuthorSnapshotResp, user: string): string {
  const f0 = a.flags.filter(f => f.level === "L0" && f.status !== "dismissed");
  const f1 = a.flags.filter(f => f.level === "L1" && f.status !== "dismissed");
  let flags = "";
  if (f0.length) flags += `<div class="pp-flag pp-flag-l0"><b>⛔ L0 确认造假标记</b>${f0.map(f =>
    `<div>事件「${esc(f.event_title || f.slug)}」 ${statusBadge(f.status)}<div class="pp-meta">${esc(f.basis)}</div></div>`).join("")}</div>`;
  if (f1.length) flags += `<div class="pp-flag pp-flag-l1"><b>⚠ L1 风险提示（非定性）</b>${f1.map(f =>
    `<div>事件「${esc(f.event_title || f.slug)}」<div class="pp-meta">${esc(f.basis)}</div></div>`).join("")}</div>`;

  const aff = (() => { let h = "", last = ""; for (const x of a.affiliations) {
    const y = String(x.start_year || "?");
    if (y !== last) { h += `<div class="pp-ev"><span class="pp-ev-y">${y}${x.end_year && x.end_year !== x.start_year ? "–" + String(x.end_year) : ""}</span>
      ${esc(x.institution)} <span class="pp-meta">[${({ auto: "自动", web: "官网", manual: "人工", llm_pending: "LLM建议" } as any)[x.source_tag] || x.source_tag}]${x.source_tag === "web" && x.verified !== 1 ? ' <span class="pp-badge pp-b-pending">待校验</span>' : ""}</span></div>`; last = y; }
  } return h || '<div class="pp-meta">暂无履历</div>'; })();

  const c = snap?.content || {};
  const snapCard = snap?.id ? `
    <div class="pp-card pp-snapcard">
      <div class="pp-card-title">AI 合成画像 ${statusBadge(snap.review_status || "pending")}
        <span class="pp-meta">#${snap.id} · ${esc(snap.generated_at || "")}</span>
        ${snap.review_status === "pending" ? `<button class="pp-btn pp-btn-sm pp-btn-ghost" data-act="review-snap" data-sid="${snap.id}" data-v="approve">批准</button>
        <button class="pp-btn pp-btn-sm pp-btn-ghost" data-act="review-snap" data-sid="${snap.id}" data-v="reject">驳回</button>` : ""}
      </div>
      ${c.focus ? `<div class="pp-snap-focus">🎯 ${esc(c.focus)}</div>` : ""}
      ${c.summary ? `<div class="pp-meta">${esc(c.summary)}</div>` : ""}
      ${c.key_contributions ? `<div class="pp-sec"><b>主要贡献</b><div class="pp-meta">${esc(c.key_contributions)}</div></div>` : ""}
      ${c.risks ? `<div class="pp-sec"><b>风险标记</b><div class="pp-meta">${esc(c.risks)}</div></div>` : ""}
    </div>` : "";

  return `
    <div class="pp-row" style="align-items:flex-start">
      <div class="pp-grow">
        ${snapCard}
        <div class="pp-card">
          <div class="pp-card-title">${esc(a.name_display)} ${a.name_zh ? `<span class="pp-meta">（${esc(a.name_zh)}）</span>` : ""}
            <span class="pp-badge ${a.tier === "core" ? "pp-b-core" : "pp-b-peripheral"}">${a.tier === "core" ? "核心层" : "外围层"}</span>
            ${a.orcid ? `<a class="pp-ext" href="${esc(a.orcid)}" target="_blank">ORCID</a>` : ""}
            ${a.openalex_id ? ` · <a class="pp-ext" href="${esc(a.openalex_id)}" target="_blank">OpenAlex</a>` : ""}
            <span class="pp-meta">${esc(a.id)}</span>
            <span class="pp-hanziwrap"><input id="pp-hanzi" placeholder="录入汉字真名" class="pp-input pp-input-sm">
            <button class="pp-btn pp-btn-sm" data-act="hanzi">保存</button></span>
          </div>
          ${flags}
          <div class="pp-card-title" style="margin-top:10px">论文（${a.papers.length}）</div>
          <table class="pp-table"><tr><th>年份</th><th>标题</th><th>期刊</th><th>被引</th><th>状态</th></tr>
          ${a.papers.map(p => `<tr><td>${p.year || ""}</td>
            <td><a class="pp-ext" href="${pmidLink(p)}" target="_blank">${esc(p.title)}</a>${p.n_flags ? ` <span class="pp-badge pp-b-retracted">标记×${p.n_flags}</span>` : ""}</td>
            <td class="pp-meta">${esc(p.journal)}</td><td>${p.cited_by_count || 0}</td><td>${flagBadges(p)}</td></tr>`).join("")}
          </table>
          <div class="pp-card-title" style="margin-top:12px">合作者（前 30）</div>
          <div class="pp-chips">${a.collaborators.map(c =>
            `<span class="pp-chip" data-act="open" data-aid="${esc(c.id)}">${esc(c.name_display)} ×${c.co_papers}</span>`).join("")}</div>
        </div>
      </div>
      <div class="pp-side">
        <div class="pp-card"><div class="pp-card-title">时间履历</div><div class="pp-timeline">${aff}</div></div>
        <div class="pp-card"><div class="pp-card-title">方向簇归属</div>
          ${a.clusters.length ? a.clusters.map(c => `<div>簇 #${c.label}（批次 ${esc(c.batch_id)}，权重 ${c.weight}）</div>`).join("") : '<div class="pp-meta">暂无</div>'}</div>
        <div class="pp-card"><div class="pp-card-title">别名</div>
          <table class="pp-table"><tr><th>别名</th><th>类型</th><th>状态</th></tr>
          ${a.aliases.map(al => `<tr><td>${esc(al.alias)}</td><td class="pp-meta">${al.alias_type}</td><td>${al.verified === 1 ? "✅" : al.verified === -1 ? "❌" : "待校对"}</td></tr>`).join("")}</table></div>
        <div class="pp-card"><div class="pp-card-title">留痕</div><div class="pp-audit">
          ${a.audit.map(l => `<div>${l.ts} · ${esc(l.actor)} · ${esc(l.action)} <span class="pp-meta">${esc(l.detail || "")}</span></div>`).join("") || "无"}</div></div>
      </div>
    </div>`;
}

// ---------- 造假事件 ----------
export async function renderEvents(user: string): Promise<string> {
  const evs = await api<FraudEvent[]>("/api/events");
  return `<div class="pp-card"><div class="pp-card-title">造假事件</div>
    ${evs.length ? `<table class="pp-table"><tr><th>#</th><th>事件</th><th>状态</th><th>论文</th><th>L0</th><th>L1</th><th></th></tr>
    ${evs.map(e => `<tr><td>${e.id}</td>
      <td><a class="pp-ext" href="javascript:;" data-open-event="${e.id}">${esc(e.title)}</a><div class="pp-meta">${esc(e.slug)}</div></td>
      <td>${statusBadge(e.status)}</td><td>${e.n_paper_flags}</td><td>${e.n_l0}</td><td>${e.n_l1}</td>
      <td>${e.status !== "confirmed" ? `<button class="pp-btn pp-btn-sm pp-btn-primary" data-confirm-event="${e.id}">人工确认</button>` : ""}</td></tr>`).join("")}</table>`
    : '<div class="pp-meta">暂无事件</div>'}</div>`;
}

export async function renderEventDetail(id: number, user: string): Promise<string> {
  const e = await api<FraudEventDetail>("/api/event/" + id);
  return `<div class="pp-card">
    <div class="pp-card-title">${esc(e.title)} ${statusBadge(e.status)}</div>
    <div class="pp-meta">${esc(e.description || "")}</div>
    <div style="margin:8px 0">${e.source_urls.map(u => `<a class="pp-ext" href="${esc(u)}" target="_blank">${esc(u)}</a>`).join("<br>")}</div>
    <div class="pp-card-title" style="margin-top:12px">论文级标记（${e.paper_flags.length}）</div>
    <table class="pp-table"><tr><th>论文</th><th>年份</th><th>类型</th><th>依据</th></tr>
      ${e.paper_flags.map(f => `<tr><td><a class="pp-ext" href="${pmidLink(f)}" target="_blank">${esc(f.title)}</a></td>
        <td>${f.year}</td><td>${({ retraction: "撤稿", correction: "更正", expression_of_concern: "关注声明", questioned: "质疑" } as any)[f.flag_type]}</td>
        <td class="pp-meta">${esc(f.note || "")}</td></tr>`).join("")}</table>
    <div class="pp-card-title" style="margin-top:12px">人员级标记</div>
    <table class="pp-table"><tr><th>研究者</th><th>级别</th><th>状态</th><th>依据</th><th>操作</th></tr>
      ${e.author_flags.map(f => `<tr>
        <td><a class="pp-ext" href="javascript:;" data-open-author="${esc(f.id)}">${esc(f.name_display)}</a></td>
        <td><span class="pp-badge pp-b-${f.level.toLowerCase()}">${f.level}</span></td>
        <td>${statusBadge(f.status)}</td><td class="pp-meta">${esc(f.basis)}</td>
        <td>${f.level === "L0" && f.status === "pending"
          ? `<input id="pp-basis-${f.id}" class="pp-input pp-input-sm" placeholder="定性依据">
             <button class="pp-btn pp-btn-sm pp-btn-danger" data-confirm-l0e="${f.id}">人工确认 L0</button>`
          : f.status !== "dismissed" && f.level === "L1" ? `<button class="pp-btn pp-btn-sm pp-btn-ghost" data-dismiss-flag="${f.id}">排除</button>` : ""}
        </td></tr>`).join("")}</table>
    <div class="pp-card-title" style="margin-top:12px">留痕</div>
    <div class="pp-audit">${e.audit.map(l => `<div>${l.ts} · ${esc(l.actor)} · ${esc(l.action)} <span class="pp-meta">${esc(l.detail || "")}</span></div>`).join("")}</div>
  </div>`;
}

// ---------- 管理台 ----------
export async function renderAdmin(user: string, domain: string): Promise<string> {
  const [sq, jq, q, aff, wv, al] = await Promise.all([
    api<SnapshotQueueItem[]>("/api/snapshot-queue").catch(() => []),
    api<Judgment[]>("/api/judgments?status=pending").catch(() => []),
    api<any[]>("/api/queue").catch(() => []),
    api<any[]>("/api/affiliation-queue").catch(() => []),
    api<WebvpnImport[]>("/api/webvpn-imports").catch(() => []),
    api<any[]>("/api/audit").catch(() => []),
  ]);
  let snapRows = "";
  if (sq.length) {
    snapRows = sq.map(r => {
      const c = (r.content && typeof r.content === "object") ? r.content : {};
      const who = r.kind === "author" ? `${esc(r.author_name || r.author_id)}` : `方向：${esc(dirName({ name: c.name, label: r.cluster_id }))}`;
      return `<tr><td>${r.id}</td><td>${r.kind === "author" ? "作者" : "方向"}</td><td>${who}</td>
        <td class="pp-meta">${esc((c.definition || c.summary || c.focus || "").slice(0, 70))}…</td>
        <td>${r.n_evidence}</td><td class="pp-meta">${esc(r.model || "")}</td>
        <td><button class="pp-btn pp-btn-sm pp-btn-primary" data-act="review" data-kind="${r.kind}" data-sid="${r.id}" data-v="approve">批准</button>
            <button class="pp-btn pp-btn-sm pp-btn-ghost" data-act="review" data-kind="${r.kind}" data-sid="${r.id}" data-v="reject">驳回</button></td></tr>`;
    }).join("");
  }
  const jRows = jq.length ? jq.map(j => `<tr><td>${j.id}</td><td>${esc(j.jtype)}</td><td>${esc(j.entity_type)}:${esc(j.entity_id)}</td>
    <td class="pp-meta">${esc(JSON.stringify(j.proposal).slice(0, 90))}…</td>
    <td><button class="pp-btn pp-btn-sm pp-btn-primary" data-act="judge" data-jid="${j.id}" data-v="accept">采纳</button>
        <button class="pp-btn pp-btn-sm pp-btn-ghost" data-act="judge" data-jid="${j.id}" data-v="reject">驳回</button></td></tr>`).join("") : "";
  const qRows = q.length ? q.map(r => `<tr><td>${esc(r.alias)}</td><td class="pp-meta">${r.alias_type}</td><td class="pp-meta">${r.source}</td>
    <td>${r.confidence ?? "-"}</td><td><a class="pp-ext" href="javascript:;" data-act="open" data-aid="${esc(r.author_id)}">${esc(r.name_display)}</a></td>
    <td><button class="pp-btn pp-btn-sm pp-btn-primary" data-act="alias" data-aid2="${r.id}" data-v="1">确认</button>
        <button class="pp-btn pp-btn-sm pp-btn-ghost" data-act="alias" data-aid2="${r.id}" data-v="0">驳回</button></td></tr>`).join("") : "";
  const affRows = aff.length ? aff.map(r => `<tr><td>${esc(r.name_display)}</td><td>${esc(r.institution)}</td>
    <td class="pp-meta">${esc(r.source_url || "")}</td>
    <td><button class="pp-btn pp-btn-sm pp-btn-primary" data-act="aff" data-affid="${r.id}">确认</button></td></tr>`).join("") : "";
  return `
    <div class="pp-card"><div class="pp-card-title">方向/作者快照审阅（LLM 合成，人工批准）</div>
      ${sq.length ? `<table class="pp-table"><tr><th>#</th><th>类型</th><th>对象</th><th>摘要</th><th>证据</th><th>合成者</th><th>操作</th></tr>${snapRows}</table>`
        : '<div class="pp-meta">无待审快照</div>'}</div>
    <div class="pp-card"><div class="pp-card-title">LLM 建议裁决队列（噪声簇 / 方向并拆 / 别名候选）</div>
      ${jq.length ? `<table class="pp-table"><tr><th>#</th><th>类型</th><th>对象</th><th>提案</th><th>操作</th></tr>${jRows}</table>`
        : '<div class="pp-meta">无待裁决建议</div>'}</div>
    <div class="pp-card"><div class="pp-card-title">机构官网信息校验（核心作者任职机构）</div>
      ${aff.length ? `<table class="pp-table"><tr><th>研究者</th><th>机构</th><th>来源</th><th>操作</th></tr>${affRows}</table>`
        : '<div class="pp-meta">无待校验机构信息</div>'}</div>
    <div class="pp-card"><div class="pp-card-title">待校对队列（别名 / 汉字名候选）</div>
      ${q.length ? `<table class="pp-table"><tr><th>别名</th><th>类型</th><th>来源</th><th>置信</th><th>研究者</th><th>操作</th></tr>${qRows}</table>`
        : '<div class="pp-meta">队列为空</div>'}</div>
    <div class="pp-card"><div class="pp-card-title">webvpn 导入批次（Scopus / CNKI / 万方）</div>
      ${wv.length ? `<table class="pp-table"><tr><th>#</th><th>域</th><th>来源</th><th>文件</th><th>记录</th><th>新增</th><th>去重</th><th>时间</th></tr>
        ${wv.map(w => `<tr><td>${w.id}</td><td>${esc(w.domain_id)}</td><td>${esc(w.source)}</td><td class="pp-meta">${esc(w.file_name || "")}</td>
          <td>${w.n_records}</td><td>${w.n_new}</td><td>${w.n_dup}</td><td class="pp-meta">${w.imported_at}</td></tr>`).join("")}</table>`
        : '<div class="pp-meta">无导入批次（webvpn 采集见 skill）</div>'}</div>
    <div class="pp-card"><div class="pp-card-title">操作留痕（最近 120 条）</div>
      <div class="pp-audit">${al.map(l => `<div>${l.ts} · ${esc(l.actor)} · ${esc(l.action)} · ${esc(l.entity_type || "")}:${esc(l.entity_id || "")} <span class="pp-meta">${esc(l.detail || "")}</span></div>`).join("")}</div></div>
    <div class="pp-card"><div class="pp-card-title">管线命令</div>
      <div class="pp-meta" style="line-height:2">
        免费源增量：<code>./update.sh</code><br>
        webvpn 导入：<code>python3 manage/webvpn.py import &lt;文件&gt; --source scopus|cnki|wanfang --domain ${esc(domain)}</code><br>
        图谱刷新：<code>python3 analyze/graph.py ${esc(domain)}</code> ｜ 方向合成上下文：<code>python3 analyze/extract.py ${esc(domain)}</code><br>
        作者画像上下文：<code>python3 analyze/extract.py ${esc(domain)} --authors</code> → 合成 → <code>python3 manage/snapshot.py apply-authors &lt;json&gt;</code><br>
        失效感知：<code>python3 manage/snapshot.py staleness ${esc(domain)}</code> ｜ 建议提案：<code>python3 manage/judgment.py propose &lt;json&gt;</code><br>
        机构官网信息：<code>python3 manage/affiliations.py add &lt;BG…&gt; --institution ... --by 你</code>
      </div></div>`;
}
