"""领域管理：新建 / 列表 / MeSH 校验 / 安全删除。

新建域示例（AI 或人工起草词表后）：
  python3 manage/domains.py new migraine --name "偏头痛" \
      --mesh "Migraine Disorders" "Migraine with Aura" \
      --keywords-strong "migraine" "migraineous" \
      --max-fetch 5000
随后运行采集管线：ingest/pubmed.py → ingest/openalex.py → analyze/graph.py
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import CONFIG_DIR, audit, connect, fetch_json, init_db

TEMPLATE = CONFIG_DIR / "_template.json"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def cmd_new(args):
    path = CONFIG_DIR / f"{args.id}.json"
    if path.exists() and not args.force:
        raise SystemExit(f"配置已存在：{path}（--force 覆盖）")
    cfg = {
        "id": args.id,
        "name": args.name or args.id,
        "description": args.description or "",
        "pubmed": {
            "mesh_terms": args.mesh or [],
            "mesh_boundary": args.mesh_boundary or [],
            "keywords_strong": args.keywords_strong or [],
            "keywords_boundary": args.keywords_boundary or [],
            "cooccur_terms": args.cooccur or [],
            "author_queries": args.author_queries or [],
            "max_fetch": args.max_fetch,
            "sort": "pub_date",
        },
        "openalex": {"batch": 100, "max_cite_seed_per_seed": 500},
        "seeds": {"cite_seed_min": 1, "seed_dois": args.seed_doi or [], "seed_pmids": []},
        "tier": {"core_min_papers": 8, "core_min_total_citations": 150},
    }
    empty = not any([cfg["pubmed"]["mesh_terms"], cfg["pubmed"]["mesh_boundary"],
                     cfg["pubmed"]["keywords_strong"], cfg["pubmed"]["keywords_boundary"],
                     cfg["pubmed"]["author_queries"]])
    if empty:
        raise SystemExit("至少提供一种收录规则（--mesh / --keywords-strong / --author-queries…）")
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[new] 已写入 {path}")
    print("下一步：")
    print(f"  python3 manage/domains.py check {args.id}   # 校验 MeSH 词表")
    print(f"  python3 ingest/pubmed.py {args.id}")
    print(f"  python3 ingest/openalex.py {args.id}")
    print(f"  python3 analyze/graph.py {args.id}")


def cmd_list(_args):
    conn = connect()
    init_db(conn)
    for f in sorted(CONFIG_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        cfg = json.loads(f.read_text(encoding="utf-8"))
        row = conn.execute(
            """SELECT (SELECT COUNT(*) FROM paper_domains WHERE domain_id=?) AS papers,
                      (SELECT COUNT(DISTINCT pa.author_id) FROM paper_authors pa
                       JOIN paper_domains pd ON pd.paper_id=pa.paper_id WHERE pd.domain_id=?) AS authors""",
            (cfg["id"], cfg["id"])).fetchone()
        print(f"{cfg['id']:<16} {cfg['name']}  ——  论文 {row['papers']} / 研究者 {row['authors']}")


def cmd_check(args):
    """MeSH 词表校验：逐词查询 NLM MeSH 库（确定性，可复现）。"""
    cfg = json.loads((CONFIG_DIR / f"{args.id}.json").read_text(encoding="utf-8"))
    terms = list(cfg["pubmed"].get("mesh_terms", [])) + list(cfg["pubmed"].get("mesh_boundary", []))
    if not terms:
        print("该域未使用 MeSH 词")
        return
    ok, bad = [], []
    for t in terms:
        try:
            js = fetch_json(f"{EUTILS}/esearch.fcgi",
                            {"db": "mesh", "term": f'"{t}"[MeSH Term]', "retmode": "json"})
            n = int(js.get("esearchresult", {}).get("count", 0))
            (ok if n > 0 else bad).append(t)
        except Exception as e:
            bad.append(f"{t} (查询失败: {e})")
        time.sleep(0.4)
    print(f"[check] 有效 {len(ok)}：{ok if ok else '-'}")
    if bad:
        print(f"[check] ⚠ 无效/未命中 {len(bad)}：{bad}")
        print("       建议：去 https://meshb.nlm.nih.gov/ 核对拼写与层级")


def cmd_remove(args):
    """安全删除域：先删域关联，再清理孤儿论文（不属于任何域）。全程留痕。"""
    conn = connect()
    init_db(conn)
    dom = conn.execute("SELECT id FROM domains WHERE id=?", (args.id,)).fetchone()
    if not dom:
        # 仅配置文件存在也允许清理
        pass
    # 1) 找出本域论文
    pids = [r["paper_id"] for r in conn.execute(
        "SELECT paper_id FROM paper_domains WHERE domain_id=?", (args.id,))]
    ph = lambda n: ",".join("?" * n)
    if pids:
        for chunk_start in range(0, len(pids), 500):
            chunk = pids[chunk_start:chunk_start + 500]
            # 只删不被其他域共享的论文
            shared = {r["paper_id"] for r in conn.execute(
                f"SELECT DISTINCT paper_id FROM paper_domains WHERE paper_id IN ({ph(len(chunk))}) AND domain_id!=?",
                chunk + [args.id])}
            orphan = [p for p in chunk if p not in shared]
            if orphan:
                conn.execute(f"DELETE FROM citations WHERE citing_id IN ({ph(len(orphan))}) OR cited_id IN ({ph(len(orphan))})",
                             orphan + orphan)
                conn.execute(f"DELETE FROM paper_flags WHERE paper_id IN ({ph(len(orphan))})", orphan)
                conn.execute(f"DELETE FROM evidence WHERE paper_id IN ({ph(len(orphan))})", orphan)
                conn.execute(f"DELETE FROM paper_domains WHERE paper_id IN ({ph(len(orphan))})", orphan)
                conn.execute(f"DELETE FROM paper_authors WHERE paper_id IN ({ph(len(orphan))})", orphan)
                conn.execute(f"DELETE FROM paper_author_staging WHERE paper_id IN ({ph(len(orphan))})", orphan)
                conn.execute(f"DELETE FROM papers WHERE id IN ({ph(len(orphan))})", orphan)
    # 2) 簇与归属
    cids = [r["id"] for r in conn.execute("SELECT id FROM clusters WHERE domain_id=?", (args.id,))]
    if cids:
        conn.execute(f"DELETE FROM author_clusters WHERE cluster_id IN ({ph(len(cids))})", cids)
        conn.execute(f"DELETE FROM clusters WHERE id IN ({ph(len(cids))})", cids)
    # 3) 其余关联
    conn.execute("DELETE FROM paper_domains WHERE domain_id=?", (args.id,))
    conn.execute("DELETE FROM author_flags WHERE event_id IN (SELECT id FROM fraud_events WHERE domain_id=?)", (args.id,))
    conn.execute("DELETE FROM paper_flags WHERE event_id IN (SELECT id FROM fraud_events WHERE domain_id=?)", (args.id,))
    conn.execute("DELETE FROM fraud_events WHERE domain_id=?", (args.id,))
    conn.execute("DELETE FROM cursors WHERE domain_id=?", (args.id,))
    conn.execute("DELETE FROM domains WHERE id=?", (args.id,))
    # 4) 孤儿作者（无任何论文链接）——先清引用行再删作者
    orphans = [r["id"] for r in conn.execute(
        "SELECT id FROM authors WHERE id NOT IN (SELECT DISTINCT author_id FROM paper_authors)")]
    for chunk_start in range(0, len(orphans), 500):
        chunk = orphans[chunk_start:chunk_start + 500]
        conn.execute(f"DELETE FROM author_aliases WHERE author_id IN ({ph(len(chunk))})", chunk)
        conn.execute(f"DELETE FROM affiliations WHERE author_id IN ({ph(len(chunk))})", chunk)
        conn.execute(f"DELETE FROM author_merges WHERE kept_id IN ({ph(len(chunk))})", chunk)
        conn.execute(f"DELETE FROM author_flags WHERE author_id IN ({ph(len(chunk))})", chunk)
        conn.execute(f"DELETE FROM author_clusters WHERE author_id IN ({ph(len(chunk))})", chunk)
        conn.execute(f"DELETE FROM authors WHERE id IN ({ph(len(chunk))})", chunk)
    audit(conn, "system", "domain.remove", "domain", args.id, {"papers_touched": len(pids)})
    conn.commit()
    # 5) 配置文件与图谱文件
    cfg_path = CONFIG_DIR / f"{args.id}.json"
    if cfg_path.exists():
        if not args.keep_config:
            cfg_path.unlink()
            print(f"[remove] 配置已删除：{cfg_path.name}")
        else:
            print(f"[remove] 配置保留：{cfg_path.name}")
    graph = Path(__file__).resolve().parent.parent / "data" / f"graph_{args.id}.json"
    if graph.exists():
        graph.unlink()
    print(f"[remove] 域 {args.id} 数据清理完成")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="领域管理")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("new", help="新建域配置")
    p1.add_argument("id")
    p1.add_argument("--name")
    p1.add_argument("--description")
    p1.add_argument("--mesh", nargs="*")
    p1.add_argument("--mesh-boundary", nargs="*")
    p1.add_argument("--keywords-strong", nargs="*")
    p1.add_argument("--keywords-boundary", nargs="*")
    p1.add_argument("--cooccur", nargs="*")
    p1.add_argument("--author-queries", nargs="*")
    p1.add_argument("--seed-doi", nargs="*")
    p1.add_argument("--max-fetch", type=int, default=10000)
    p1.add_argument("--force", action="store_true")
    p2 = sub.add_parser("list")
    p3 = sub.add_parser("check", help="MeSH 词表校验")
    p3.add_argument("id")
    p4 = sub.add_parser("remove", help="删除域及其数据")
    p4.add_argument("id")
    p4.add_argument("--keep-config", action="store_true")
    args = ap.parse_args()
    {"new": cmd_new, "list": cmd_list, "check": cmd_check, "remove": cmd_remove}[args.cmd](args)
