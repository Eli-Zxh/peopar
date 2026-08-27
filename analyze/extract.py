"""合成上下文包导出：为「方向快照 / 作者画像」合成准备确定性数据。

用法：
  python3 analyze/extract.py <domain_id> [--min-size 3] [--max-papers 60] [--top 30]    方向级
  python3 analyze/extract.py <domain_id> --authors [--scope core-flagged|--ids ...|--from-file f] [--batch 40]  作者级

产物：
  data/pack_<domain>.json            方向级上下文包
  data/pack_authors_<domain>_<n>.json  作者级上下文包（每包 ≤batch 人，控制单次合成规模）
原则：本文件只做确定性导出，不做任何语义加工；合成由 agent 按 prompts/ 模板执行。
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, init_db

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def domain_paper_ids(conn, domain_id):
    return [r["paper_id"] for r in conn.execute(
        "SELECT paper_id FROM paper_domains WHERE domain_id=?", (domain_id,))]


def cluster_paper_ids(conn, cluster_id, pid_set):
    rows = conn.execute(
        """SELECT DISTINCT pa.paper_id FROM paper_authors pa
           JOIN author_clusters ac ON ac.author_id = pa.author_id
           WHERE ac.cluster_id=?""", (cluster_id,)).fetchall()
    return [r["paper_id"] for r in rows if r["paper_id"] in pid_set]


def export_pack(domain_id, min_size=3, max_papers=60, top=30):
    conn = connect()
    init_db(conn)
    dom = conn.execute("SELECT id, name FROM domains WHERE id=?", (domain_id,)).fetchone()
    if not dom:
        raise SystemExit(f"域不存在：{domain_id}")
    pids = domain_paper_ids(conn, domain_id)
    pid_set = set(pids)

    clusters = conn.execute(
        """SELECT c.id, c.label, c.name, COUNT(ac.author_id) AS size
           FROM clusters c LEFT JOIN author_clusters ac ON ac.cluster_id=c.id
           WHERE c.domain_id=? AND c.batch_id=
             (SELECT MAX(batch_id) FROM clusters WHERE domain_id=?)
           GROUP BY c.id HAVING size>=?
           ORDER BY size DESC""", (domain_id, domain_id, min_size)).fetchall()

    out_clusters = []
    for c in clusters[:top]:
        cid, csize = c["id"], c["size"]
        # 簇内作者（按论文数排序）
        authors = conn.execute(
            """SELECT a.id, a.name_display, a.tier, COUNT(DISTINCT pa.paper_id) AS n
               FROM author_clusters ac JOIN authors a ON a.id=ac.author_id
               LEFT JOIN paper_authors pa ON pa.author_id=a.id AND pa.paper_id IN
                   (SELECT paper_id FROM paper_domains WHERE domain_id=?)
               WHERE ac.cluster_id=? GROUP BY a.id ORDER BY n DESC, a.name_display LIMIT 12""",
            (domain_id, cid)).fetchall()
        # 簇内论文（按被引排序截断）
        cp = cluster_paper_ids(conn, cid, pid_set)
        papers = []
        for chunk_start in range(0, len(cp), 500):
            chunk = cp[chunk_start:chunk_start + 500]
            ph = ",".join("?" * len(chunk))
            papers += conn.execute(
                f"""SELECT p.id, p.pmid, p.doi, p.title, p.year, p.journal,
                           p.first_author_norm, p.cited_by_count, p.retraction_status,
                           p.mesh, pd.rule
                    FROM papers p JOIN paper_domains pd ON pd.paper_id=p.id AND pd.domain_id=?
                    WHERE p.id IN ({ph})""", [domain_id] + chunk).fetchall()
        papers.sort(key=lambda r: (-(r["cited_by_count"] or 0), -(r["year"] or 0)))
        year_hist = Counter(str(r["year"]) for r in papers if r["year"])
        mesh_freq = Counter()
        for r in papers:
            if r["mesh"]:
                try:
                    for m in json.loads(r["mesh"]):
                        mesh_freq[m] += 1
                except (json.JSONDecodeError, TypeError):
                    pass
        rule_hist = Counter(r["rule"] for r in papers)
        snap = conn.execute(
            """SELECT id, review_status FROM snapshots WHERE cluster_id=?
               ORDER BY id DESC LIMIT 1""", (cid,)).fetchone()
        out_clusters.append({
            "cluster_id": cid, "label": c["label"], "name": c["name"], "size": csize,
            "existing_snapshot": dict(snap) if snap else None,
            "top_authors": [{"id": a["id"], "name": a["name_display"],
                             "tier": a["tier"], "papers": a["n"]} for a in authors],
            "paper_count": len(cp),
            "papers": [{"paper_id": p["id"], "pmid": p["pmid"], "doi": p["doi"],
                        "title": p["title"], "year": p["year"], "journal": p["journal"],
                        "first_author": p["first_author_norm"],
                        "cited_by": p["cited_by_count"], "retraction": p["retraction_status"],
                        "rule": p["rule"]} for p in papers[:max_papers]],
            "year_hist": dict(sorted(year_hist.items())),
            "mesh_freq": mesh_freq.most_common(15),
            "rule_hist": dict(rule_hist),
        })

    pack = {
        "domain": domain_id,
        "domain_name": dom["name"],
        "stats": {
            "papers": len(pids),
            "clusters_total": conn.execute(
                "SELECT COUNT(*) FROM clusters WHERE domain_id=?", (domain_id,)).fetchone()[0],
            "clusters_exported": len(out_clusters),
            "filters": {"min_size": min_size, "max_papers": max_papers, "top": top},
        },
        "clusters": out_clusters,
    }
    out = DATA_DIR / f"pack_{domain_id}.json"
    out.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    audit(conn, "system", "extract.pack", "domain", domain_id,
          {"clusters": len(out_clusters), "file": out.name})
    conn.commit()
    print(f"[extract] {out}：{len(out_clusters)} 个簇（共 {pack['stats']['clusters_total']} 个）")


def export_author_packs(domain_id, scope="core-flagged", ids=None, batch=40):
    """作者级上下文包导出：每包 ≤batch 人 → data/pack_authors_<domain>_<n>.json。"""
    conn = connect()
    init_db(conn)
    dom = conn.execute("SELECT id, name FROM domains WHERE id=?", (domain_id,)).fetchone()
    if not dom:
        raise SystemExit(f"域不存在：{domain_id}")
    if ids:
        target = [i.strip() for i in ids.split(",") if i.strip()]
    elif scope == "core-flagged":
        target = [r["id"] for r in conn.execute(
            """SELECT DISTINCT a.id FROM authors a
               WHERE a.tier='core'
                  OR a.id IN (SELECT author_id FROM author_flags WHERE status!='dismissed')""")]
    elif scope == "core":
        target = [r["id"] for r in conn.execute("SELECT id FROM authors WHERE tier='core'")]
    else:
        raise SystemExit(f"未知 scope：{scope}")
    print(f"[extract-authors] {domain_id}: 目标 {len(target)} 人（{scope}）")
    n_pack = 0
    for start in range(0, len(target), batch):
        chunk = target[start:start + batch]
        out_authors = []
        for aid in chunk:
            a = conn.execute(
                "SELECT id, name_display, name_zh, tier, orcid, openalex_id FROM authors WHERE id=?",
                (aid,)).fetchone()
            if not a:
                continue
            papers = conn.execute(
                """SELECT p.id, p.pmid, p.doi, p.title, p.year, p.journal, p.cited_by_count,
                          p.retraction_status, pd.rule
                   FROM paper_authors pa JOIN papers p ON p.id=pa.paper_id
                   JOIN paper_domains pd ON pd.paper_id=p.id AND pd.domain_id=?
                   WHERE pa.author_id=? ORDER BY p.cited_by_count DESC""",
                (domain_id, aid)).fetchall()
            truncated = False
            paper_list = [dict(r) for r in papers]
            if len(paper_list) > 80:
                paper_list = paper_list[:80]
                truncated = True
            clusters = conn.execute(
                """SELECT c.id, c.label, c.name, ac.weight FROM author_clusters ac
                   JOIN clusters c ON c.id=ac.cluster_id
                   WHERE ac.author_id=? AND c.domain_id=?
                   ORDER BY ac.weight DESC LIMIT 5""", (aid, domain_id)).fetchall()
            collabs = conn.execute(
                """SELECT b.id, b.name_display, COUNT(*) AS co_papers
                   FROM paper_authors pa1
                   JOIN paper_authors pa2 ON pa2.paper_id=pa1.paper_id AND pa2.author_id!=pa1.author_id
                   JOIN authors b ON b.id=pa2.author_id
                   WHERE pa1.author_id=? GROUP BY b.id ORDER BY co_papers DESC LIMIT 10""",
                (aid,)).fetchall()
            flags = conn.execute(
                """SELECT af.level, af.status, af.basis, fe.title AS event
                   FROM author_flags af LEFT JOIN fraud_events fe ON fe.id=af.event_id
                   WHERE af.author_id=?""", (aid,)).fetchall()
            aliases = conn.execute(
                "SELECT alias, alias_type, source, verified FROM author_aliases WHERE author_id=?",
                (aid,)).fetchall()
            affs = conn.execute(
                "SELECT institution, start_year, end_year, source_tag, verified FROM affiliations "
                "WHERE author_id=? ORDER BY start_year", (aid,)).fetchall()
            out_authors.append({
                "author_id": aid,
                "name_display": a["name_display"], "name_zh": a["name_zh"],
                "tier": a["tier"], "orcid": a["orcid"], "openalex_id": a["openalex_id"],
                "papers": [{"paper_id": p["id"], "pmid": p["pmid"], "doi": p["doi"],
                            "title": p["title"], "year": p["year"], "journal": p["journal"],
                            "cited_by": p["cited_by_count"], "retraction": p["retraction_status"],
                            "rule": p["rule"]} for p in paper_list],
                "papers_truncated": truncated,
                "clusters": [dict(c) for c in clusters],
                "collaborators": [dict(c) for c in collabs],
                "flags": [dict(f) for f in flags],
                "aliases": [dict(al) for al in aliases],
                "affiliations": [dict(x) for x in affs],
            })
        n_pack += 1
        out = DATA_DIR / f"pack_authors_{domain_id}_{n_pack}.json"
        out.write_text(json.dumps({
            "domain": domain_id, "domain_name": dom["name"],
            "batch": n_pack, "scope": scope,
            "authors": out_authors,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        audit(conn, "system", "extract.authors", "domain", domain_id,
              {"batch": n_pack, "authors": len(out_authors), "file": out.name})
        print(f"[extract-authors] {out.name}：{len(out_authors)} 人")
    conn.commit()
    print(f"[extract-authors] 共 {n_pack} 包 / {len(target)} 人")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="导出合成上下文包")
    ap.add_argument("domain")
    ap.add_argument("--min-size", type=int, default=3, help="簇最小作者数")
    ap.add_argument("--max-papers", type=int, default=60, help="每簇携带论文上限（按被引排序）")
    ap.add_argument("--top", type=int, default=30, help="导出簇数上限（按规模排序）")
    ap.add_argument("--authors", action="store_true", help="作者级导出模式")
    ap.add_argument("--scope", default="core-flagged",
                    help="作者级：core-flagged（核心∪被标记，默认）/ core / 或配合 --ids")
    ap.add_argument("--ids", help="作者级：显式 BG id 列表（逗号分隔）")
    ap.add_argument("--from-file", help="作者级：从文件逐行读取 BG id")
    ap.add_argument("--batch", type=int, default=40, help="作者级：每包人数")
    args = ap.parse_args()
    if args.authors:
        ids = args.ids
        if args.from_file:
            ids = ",".join(l.strip() for l in Path(args.from_file).read_text(encoding="utf-8").splitlines()
                           if l.strip())
        export_author_packs(args.domain, args.scope, ids, args.batch)
    else:
        export_pack(args.domain, args.min_size, args.max_papers, args.top)
