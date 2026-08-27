"""方向快照管理：agent 合成产物的锚定校验、入库与审阅。

子命令：
  apply  <json>                 入库簇级快照（先做确定性锚定校验）
  note   <domain> <md> [--by X] 域级概览笔记校验并落盘到 notes/
  list   [domain]               快照清单
  review <id> approve|reject --by <署名>   人工审阅（与网页按钮等价）

原则：
- 收录判定无 LLM；合成产物一律锚定校验（引用论文必须真实存在于该簇/域）后入库，默认待人工审阅。
- 同簇重复合成：旧快照置 superseded，新快照 supersedes 指向旧 id（只增不改）。
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, basis_signature, connect, init_db

ROOT = Path(__file__).resolve().parent.parent
NOTES_DIR = ROOT / "notes"

PMID_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{5,9})")
DOI_RE = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s)\]>]+)")


def cluster_paper_ids(conn, cluster_id):
    """簇内论文 = 簇内作者的论文（与 extract 口径一致）。"""
    return {r["paper_id"] for r in conn.execute(
        """SELECT DISTINCT pa.paper_id FROM paper_authors pa
           JOIN author_clusters ac ON ac.author_id=pa.author_id
           WHERE ac.cluster_id=?""", (cluster_id,))}


def cmd_apply(args):
    doc = json.loads(Path(args.json).read_text(encoding="utf-8"))
    conn = connect()
    init_db(conn)
    domain = doc["domain"]
    if not conn.execute("SELECT id FROM domains WHERE id=?", (domain,)).fetchone():
        raise SystemExit(f"域不存在：{domain}")
    actor = f"user:{args.by}" if args.by else "agent"
    ok, fail = 0, []
    for item in doc["snapshots"]:
        cid = item["cluster_id"]
        c = conn.execute("SELECT id, domain_id FROM clusters WHERE id=?", (cid,)).fetchone()
        if not c or c["domain_id"] != domain:
            fail.append({"cluster_id": cid, "reason": "簇不存在或不属于该域"})
            continue
        cp = cluster_paper_ids(conn, cid)
        reps = item.get("representative_paper_ids", [])
        sups = item.get("supporting_paper_ids", [])
        bad = [p for p in reps + sups if p not in cp]
        if bad:
            fail.append({"cluster_id": cid, "reason": f"引用了不属于该簇的论文 {bad[:5]}…",
                         "bad_ids": bad})
            continue
        content = json.dumps({k: item[k] for k in
                              ("name", "definition", "current_conclusions", "timeline",
                               "controversies") if k in item},
                             ensure_ascii=False)
        sig = basis_signature(cp)  # 合成时刻论文集合指纹（失效感知）
        old = conn.execute("SELECT id FROM snapshots WHERE cluster_id=? ORDER BY id DESC LIMIT 1",
                           (cid,)).fetchone()
        cur = conn.execute(
            """INSERT INTO snapshots(cluster_id, content, model, prompt_ver, basis_signature,
                                     supersedes, review_status, generated_at)
               VALUES(?,?,?,?,?,?,'pending', datetime('now'))""",
            (cid, content, doc.get("model"), doc.get("prompt_ver"), sig,
             old["id"] if old else None))
        sid = cur.lastrowid
        for pid in reps:
            conn.execute("INSERT OR IGNORE INTO evidence(snapshot_id, paper_id, role) VALUES(?,?,?)",
                         (sid, pid, "representative"))
        for pid in sups:
            conn.execute("INSERT OR IGNORE INTO evidence(snapshot_id, paper_id, role) VALUES(?,?,?)",
                         (sid, pid, "supporting"))
        if old:
            conn.execute("UPDATE snapshots SET status='superseded' WHERE id=?", (old["id"],))
        # 方向名落簇表：簇命名由最新快照驱动（clusters.name 仅作展示名，非簇成员历史）
        if item.get("name"):
            conn.execute("UPDATE clusters SET name=? WHERE id=?", (item["name"], cid))
        audit(conn, actor, "snapshot.apply", "snapshot", sid,
              {"cluster_id": cid, "name": item.get("name"), "evidence": len(reps) + len(sups)})
        ok += 1
    conn.commit()
    print(f"[apply] 入库 {ok} 条，拦截 {len(fail)} 条")
    for f in fail:
        print(f"  ⚠ 簇{f['cluster_id']}: {f['reason']}")
    if fail:
        raise SystemExit(1)


def extract_citations(md_text):
    pmids = set(PMID_RE.findall(md_text))
    dois = {d.rstrip(".,;") for d in DOI_RE.findall(md_text)}
    return pmids, dois


def cmd_note(args):
    conn = connect()
    init_db(conn)
    if not conn.execute("SELECT id FROM domains WHERE id=?", (args.domain,)).fetchone():
        raise SystemExit(f"域不存在：{args.domain}")
    src = Path(args.md)
    text = src.read_text(encoding="utf-8")
    pmids, dois = extract_citations(text)
    known_pmids = {r["pmid"] for r in conn.execute(
        """SELECT DISTINCT p.pmid FROM papers p JOIN paper_domains pd ON pd.paper_id=p.id
           WHERE pd.domain_id=? AND p.pmid IS NOT NULL""", (args.domain,))}
    known_dois = {r["doi"] for r in conn.execute(
        """SELECT DISTINCT p.doi FROM papers p JOIN paper_domains pd ON pd.paper_id=p.id
           WHERE pd.domain_id=? AND p.doi IS NOT NULL""", (args.domain,))}
    bad_pmids = sorted(p for p in pmids if p not in known_pmids)
    bad_dois = sorted(d for d in dois if d not in known_dois)
    n_cited = len(pmids) + len(dois)
    if n_cited == 0:
        print("⚠ 笔记中未发现 PubMed/DOI 链接——概览笔记必须带来源")
        if not args.force:
            raise SystemExit(1)
    if bad_pmids or bad_dois:
        print(f"⚠ 锚定校验失败：{len(bad_pmids)} 个 PMID、{len(bad_dois)} 个 DOI 不在该域库内")
        for p in bad_pmids[:10]:
            print(f"  PMID {p}")
        for d in bad_dois[:10]:
            print(f"  DOI {d}")
        if not args.force:
            raise SystemExit(1)
    NOTES_DIR.mkdir(exist_ok=True)
    dest = NOTES_DIR / f"{args.domain}_overview.md"
    dest.write_text(text, encoding="utf-8")
    actor = f"user:{args.by}" if args.by else "agent"
    audit(conn, actor, "snapshot.note", "domain", args.domain,
          {"file": dest.name, "citations": n_cited, "forced": bool(args.force)})
    conn.commit()
    print(f"[note] 已写入 {dest}（引用 {n_cited} 条，未锚定 {len(bad_pmids) + len(bad_dois)} 条）")


def author_domain_papers(conn, author_id, domain_id):
    """该作者在该域内的论文 id 集合。"""
    return {r["paper_id"] for r in conn.execute(
        """SELECT DISTINCT pa.paper_id FROM paper_authors pa
           JOIN paper_domains pd ON pd.paper_id=pa.paper_id
           WHERE pa.author_id=? AND pd.domain_id=?""", (author_id, domain_id))}


def cmd_apply_authors(args):
    """作者级画像入库：锚定校验（author ∈ 域；representative ⊆ 该作者论文）→ pending 待审。"""
    doc = json.loads(Path(args.json).read_text(encoding="utf-8"))
    conn = connect()
    init_db(conn)
    domain = doc["domain"]
    if not conn.execute("SELECT id FROM domains WHERE id=?", (domain,)).fetchone():
        raise SystemExit(f"域不存在：{domain}")
    actor = f"user:{args.by}" if args.by else "agent"
    ok, fail = 0, []
    for item in doc.get("author_snapshots", []):
        aid = item["author_id"]
        a = conn.execute("SELECT id FROM authors WHERE id=?", (aid,)).fetchone()
        if not a:
            fail.append({"author_id": aid, "reason": "作者不存在"})
            continue
        papers = author_domain_papers(conn, aid, domain)
        if not papers:
            fail.append({"author_id": aid, "reason": "该作者不在域内（无域论文）"})
            continue
        reps = item.get("representative_paper_ids", [])
        bad = [p for p in reps if p not in papers]
        if bad:
            fail.append({"author_id": aid, "reason": f"代表论文不属于该作者域内论文 {bad[:5]}…"})
            continue
        content = json.dumps({k: item[k] for k in
                              ("focus", "summary", "key_contributions", "risks",
                               "representative_paper_ids") if k in item},
                             ensure_ascii=False)
        sig = basis_signature(papers)
        old = conn.execute(
            "SELECT id FROM author_snapshots WHERE author_id=? ORDER BY id DESC LIMIT 1",
            (aid,)).fetchone()
        cur = conn.execute(
            """INSERT INTO author_snapshots(author_id, content, model, prompt_ver, basis_signature,
               supersedes, review_status, generated_at)
               VALUES(?,?,?,?,?,?,'pending', datetime('now'))""",
            (aid, content, doc.get("model"), doc.get("prompt_ver"), sig,
             old["id"] if old else None))
        sid = cur.lastrowid
        # 作者画像的代表论文存于 content.representative_paper_ids（evidence 表仅服务簇快照，
        # 其 snapshot_id FK 指向 snapshots 表，作者画像不写入）
        if old:
            conn.execute("UPDATE author_snapshots SET status='superseded' WHERE id=?", (old["id"],))
        audit(conn, actor, "author_snapshot.apply", "author", aid,
              {"snapshot_id": sid, "papers": len(papers), "evidence": len(reps)})
        ok += 1
    conn.commit()
    print(f"[apply-authors] 入库 {ok} 条，拦截 {len(fail)} 条")
    for f in fail:
        print(f"  ⚠ {f['author_id']}: {f['reason']}")
    if fail:
        raise SystemExit(1)


def cmd_staleness(args):
    """失效感知：active 快照的论文集合指纹与当前不一致 → affected_pending_review。不自动重合成。"""
    conn = connect()
    init_db(conn)
    if not conn.execute("SELECT id FROM domains WHERE id=?", (args.domain,)).fetchone():
        raise SystemExit(f"域不存在：{args.domain}")
    n_aff = 0
    # 簇级快照
    for s in conn.execute(
            """SELECT s.id, s.cluster_id, s.basis_signature FROM snapshots s
               JOIN clusters c ON c.id=s.cluster_id
               WHERE c.domain_id=? AND s.status='active'""", (args.domain,)).fetchall():
        if not s["basis_signature"]:
            continue
        cur_sig = basis_signature(cluster_paper_ids(conn, s["cluster_id"]))
        if cur_sig != s["basis_signature"]:
            conn.execute("UPDATE snapshots SET status='affected_pending_review' WHERE id=?",
                         (s["id"],))
            audit(conn, "system", "snapshot.stale", "snapshot", s["id"],
                  {"cluster_id": s["cluster_id"], "domain": args.domain})
            n_aff += 1
    # 作者级快照
    for s in conn.execute(
            "SELECT id, author_id, basis_signature FROM author_snapshots WHERE status='active'").fetchall():
        if not s["basis_signature"]:
            continue
        papers = author_domain_papers(conn, s["author_id"], args.domain)
        if not papers:
            continue
        cur_sig = basis_signature(papers)
        if cur_sig != s["basis_signature"]:
            conn.execute("UPDATE author_snapshots SET status='affected_pending_review' WHERE id=?",
                         (s["id"],))
            audit(conn, "system", "author_snapshot.stale", "author", s["author_id"],
                  {"snapshot_id": s["id"], "domain": args.domain})
            n_aff += 1
    conn.commit()
    print(f"[staleness] {args.domain}: 标记受影响 {n_aff} 条快照（不自动重合成）")


def cmd_list(args):
    conn = connect()
    init_db(conn)
    inner = (
        """SELECT s.id, c.domain_id, s.cluster_id, s.content, s.model, s.review_status,
                  s.generated_at, (SELECT COUNT(*) FROM evidence e WHERE e.snapshot_id=s.id) AS ev,
                  'cluster' AS kind, NULL AS author_id, NULL AS author_name
           FROM snapshots s JOIN clusters c ON c.id=s.cluster_id"""
        + (" WHERE c.domain_id=?" if args.domain else "")
        + """ UNION ALL SELECT s.id, NULL, NULL, s.content, s.model, s.review_status, s.generated_at,
                  (SELECT COUNT(*) FROM evidence e WHERE e.snapshot_id=s.id), 'author',
                  s.author_id, a.name_display
           FROM author_snapshots s JOIN authors a ON a.id=s.author_id""")
    q = "SELECT * FROM (" + inner + ") ORDER BY id DESC"
    rows = conn.execute(q, (args.domain,) if args.domain else ()).fetchall()
    for r in rows:
        if r["kind"] == "cluster":
            name = json.loads(r["content"]).get("name", "-")
            who = f"簇{r['cluster_id']}"
        else:
            name = (json.loads(r["content"]).get("focus") or "-")[:24]
            who = f"{r['author_name']} [{r['author_id']}]"
        print(f"#{r['id']:<4} {r['kind']:<8} {who:<28} {name[:34]:<36} "
              f"{r['review_status']:<8} 证据{r['ev']} {r['generated_at']}")
    if not rows:
        print("（无快照）")


def cmd_review(args):
    if args.action not in ("approve", "reject"):
        raise SystemExit("action 必须是 approve 或 reject")
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT id, review_status FROM snapshots WHERE id=?",
                       (args.id,)).fetchone()
    if not row:
        raise SystemExit(f"快照不存在：{args.id}")
    status = "approved" if args.action == "approve" else "rejected"
    conn.execute("UPDATE snapshots SET review_status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
                 (status, f"user:{args.by}" if args.by else "user",
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"), args.id))
    audit(conn, f"user:{args.by}" if args.by else "user", f"snapshot.{args.action}",
          "snapshot", args.id, None)
    conn.commit()
    print(f"[review] 快照 #{args.id} → {status}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="方向快照管理")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("apply", help="入库簇级快照（锚定校验）")
    p1.add_argument("json")
    p1.add_argument("--by", help="操作署名")
    p2 = sub.add_parser("note", help="域级概览笔记校验落盘")
    p2.add_argument("domain")
    p2.add_argument("md")
    p2.add_argument("--by")
    p2.add_argument("--force", action="store_true", help="允许未锚定引用（慎用）")
    p2b = sub.add_parser("apply-authors", help="入库作者级画像（锚定校验）")
    p2b.add_argument("json")
    p2b.add_argument("--by")
    p2c = sub.add_parser("staleness", help="失效感知：标记论文集合已变化的快照")
    p2c.add_argument("domain")
    p3 = sub.add_parser("list")
    p3.add_argument("domain", nargs="?")
    p4 = sub.add_parser("review", help="人工审阅")
    p4.add_argument("id", type=int)
    p4.add_argument("action")
    p4.add_argument("--by", required=True)
    args = ap.parse_args()
    {"apply": cmd_apply, "note": cmd_note, "apply-authors": cmd_apply_authors,
     "staleness": cmd_staleness, "list": cmd_list, "review": cmd_review}[args.cmd](args)
