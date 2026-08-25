"""共著图谱：边构建、标签传播社区发现（纯标准库）、双层制升核、物化簇、导出可视化 JSON。

簇只增不改：每次计算生成新 batch，历史 batch 保留。
"""
import argparse
import itertools
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, ensure_domain, init_db

MAX_AUTHORS_FOR_EDGES = 25  # 超大作者名单论文（联盟）不产生全对边


def domain_paper_ids(conn, domain_id):
    return [r["paper_id"] for r in conn.execute(
        "SELECT paper_id FROM paper_domains WHERE domain_id=?", (domain_id,))]


def build_coauthor_edges(conn, domain_id):
    """返回 {author_id: {author_id: weight}} 与作者统计。"""
    pids = domain_paper_ids(conn, domain_id)
    edges = defaultdict(Counter)
    papers_of = defaultdict(set)
    for chunk_start in range(0, len(pids), 500):
        chunk = pids[chunk_start:chunk_start + 500]
        ph = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT paper_id, author_id FROM paper_authors WHERE paper_id IN ({ph})", chunk).fetchall()
        by_paper = defaultdict(list)
        for r in rows:
            by_paper[r["paper_id"]].append(r["author_id"])
            papers_of[r["author_id"]].add(r["paper_id"])
        for _, authors in by_paper.items():
            authors = list(dict.fromkeys(authors))
            if len(authors) > MAX_AUTHORS_FOR_EDGES:
                continue
            for a, b in itertools.combinations(authors, 2):
                edges[a][b] += 1
                edges[b][a] += 1
    return edges, papers_of


def label_propagation(edges, papers_of, seed=42):
    """标签传播社区发现。按度降序更新，确定性随机种子。"""
    rng = random.Random(seed)
    nodes = set(edges) | set(papers_of)
    label = {n: i for i, n in enumerate(sorted(nodes))}
    order = sorted(nodes, key=lambda n: (-len(edges.get(n, {})), n))
    for _ in range(30):
        changed = False
        for n in order:
            nbr = edges.get(n)
            if not nbr:
                continue
            cnt = Counter()
            for m, w in nbr.items():
                cnt[label[m]] += w
            best = max(cnt.values())
            cands = sorted([l for l, c in cnt.items() if c == best])
            new = cands[0] if len(cands) == 1 else rng.choice(cands)
            if new != label[n]:
                label[n] = new
                changed = True
        if not changed:
            break
    # 压缩标签
    remap, out = {}, {}
    for n in sorted(label, key=lambda x: label[x]):
        remap.setdefault(label[n], len(remap))
        out[n] = remap[label[n]]
    return out


def refresh(domain_id: str):
    conn = connect()
    init_db(conn)
    cfg = ensure_domain(conn, domain_id)
    batch_id = f"{domain_id}-{date.today().isoformat()}"
    edges, papers_of = build_coauthor_edges(conn, domain_id)
    labels = label_propagation(edges, papers_of)
    # 物化簇（只增）
    sizes = Counter(labels.values())
    cluster_id_by_label = {}
    for lb, sz in sorted(sizes.items(), key=lambda kv: -kv[1]):
        if sz < 2:
            continue  # 单人簇不入方向簇表
        row = conn.execute("SELECT id FROM clusters WHERE domain_id=? AND batch_id=? AND label=?",
                           (domain_id, batch_id, lb)).fetchone()
        if row:
            cid = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO clusters(domain_id, label, batch_id, signature) VALUES(?,?,?,?)",
                (domain_id, lb, batch_id, json.dumps({"size": sz}, ensure_ascii=False)))
            cid = cur.lastrowid
        cluster_id_by_label[lb] = cid
    # 作者→簇（本批次）
    conn.execute("DELETE FROM author_clusters WHERE cluster_id IN (SELECT id FROM clusters WHERE batch_id=?)", (batch_id,))
    for a, lb in labels.items():
        cid = cluster_id_by_label.get(lb)
        if cid is None:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO author_clusters(author_id, cluster_id, weight) VALUES(?,?,?)",
            (a, cid, len(papers_of.get(a, ()))))
    # 双层制升核
    tier = cfg.get("tier", {})
    min_papers = tier.get("core_min_papers", 8)
    min_cit = tier.get("core_min_total_citations", 150)
    pids = domain_paper_ids(conn, domain_id)
    cit = Counter()
    for chunk_start in range(0, len(pids), 500):
        chunk = pids[chunk_start:chunk_start + 500]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT pa.author_id, SUM(p.cited_by_count) AS c FROM paper_authors pa "
                f"JOIN papers p ON p.id=pa.paper_id WHERE pa.paper_id IN ({ph}) GROUP BY pa.author_id",
                chunk):
            cit[r["author_id"]] = r["c"] or 0
    promoted = 0
    for a, n in papers_of.items():
        if len(n) >= min_papers or cit.get(a, 0) >= min_cit:
            row = conn.execute("SELECT tier FROM authors WHERE id=?", (a,)).fetchone()
            if row and row["tier"] != "core":
                conn.execute(
                    "UPDATE authors SET tier='core', tier_reason=?, updated_at=datetime('now') WHERE id=?",
                    (f"papers={len(n)},citations={cit.get(a,0)}", a))
                promoted += 1
    # 导出图谱 JSON
    out = export(conn, domain_id, labels, edges, papers_of, cit, cluster_id_by_label)
    audit(conn, "system", "analyze.refresh", "domain", domain_id,
          {"batch_id": batch_id, "authors": len(papers_of),
           "clusters": len(cluster_id_by_label), "promoted_core": promoted})
    conn.commit()
    print(f"[graph] {domain_id}: 作者 {len(papers_of)}，簇 {len(cluster_id_by_label)}，升核 {promoted}")
    return out


def export(conn, domain_id, labels, edges, papers_of, cit, cluster_id_by_label):
    """导出可视化 JSON（含造假标记叠加）。"""
    flags_a = defaultdict(dict)
    for r in conn.execute(
            """SELECT af.author_id, af.level, af.status, fe.title AS event FROM author_flags af
               LEFT JOIN fraud_events fe ON fe.id=af.event_id WHERE af.status!='dismissed'"""):
        flags_a[r["author_id"]][r["level"]] = {"status": r["status"], "event": r["event"]}
    paper_flags_by_author = defaultdict(int)
    for r in conn.execute(
            """SELECT pa.author_id, COUNT(DISTINCT pf.paper_id) AS n FROM paper_flags pf
               JOIN paper_authors pa ON pa.paper_id=pf.paper_id GROUP BY pa.author_id"""):
        paper_flags_by_author[r["author_id"]] = r["n"]
    nodes, node_ids = [], set()
    for a in sorted(papers_of):
        row = conn.execute(
            "SELECT id, name_display, name_zh, tier, orcid, openalex_id, note FROM authors WHERE id=?",
            (a,)).fetchone()
        if not row:
            continue
        if len(papers_of[a]) < 2 and row["tier"] != "core" and a not in flags_a:
            continue  # 单篇外围作者不上图（仍在库内与簇计算中）
        nodes.append({
            "id": row["id"], "name": row["name_display"], "zh": row["name_zh"],
            "tier": row["tier"], "cluster": labels.get(a),
            "papers": len(papers_of[a]), "citations": cit.get(a, 0),
            "l0": "L0" in flags_a[a], "l1": "L1" in flags_a[a],
            "flagged_papers": paper_flags_by_author.get(a, 0),
            "note": row["note"],
        })
        node_ids.add(a)
    edge_list = []
    seen = set()
    for a, nbrs in edges.items():
        if a not in node_ids:
            continue
        for b, w in nbrs.items():
            if b not in node_ids:
                continue
            k = (a, b) if a < b else (b, a)
            if k in seen:
                continue
            seen.add(k)
            edge_list.append({"source": a, "target": b, "weight": w})
    clusters_out = []
    for lb, cid in cluster_id_by_label.items():
        members = [n for n in nodes if n["cluster"] == lb]
        top = sorted(members, key=lambda x: -x["papers"])[:5]
        clusters_out.append({
            "label": lb, "cluster_id": cid, "size": len(members),
            "top": [{"id": m["id"], "name": m["name"], "papers": m["papers"]} for m in top],
        })
    data = {"domain": domain_id, "nodes": nodes, "edges": edge_list, "clusters": clusters_out}
    out_path = Path(__file__).resolve().parent.parent / "data" / f"graph_{domain_id}.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"[graph] 导出 {out_path.name}: {len(nodes)} 节点 / {len(edge_list)} 边")
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="共著图谱刷新")
    ap.add_argument("domain")
    args = ap.parse_args()
    refresh(args.domain)
