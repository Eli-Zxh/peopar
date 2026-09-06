"""一句话关键词（keynote）入库管理：论文 keynote → papers.keynote。

子命令：
  apply-papers <json>         入库（{domain, paper_keynotes:[{paper_id, keynote}]}）
  list-pending [--domain X]   当前仍缺 keynote 的论文数/示例（供批次规划）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, init_db


def cmd_apply_papers(args):
    doc = json.loads(Path(args.json).read_text(encoding="utf-8"))
    conn = connect()
    init_db(conn)
    domain = doc["domain"]
    domain_papers = {str(r["id"]) for r in conn.execute(
        "SELECT p.id FROM papers p JOIN paper_domains pd ON pd.paper_id=p.id WHERE pd.domain_id=?",
        (domain,))}
    n = 0
    for item in doc.get("paper_keynotes", []):
        pid = str(item["paper_id"])
        if pid not in domain_papers:
            print(f"  ⚠ paper {pid} 不在域内")
            continue
        txt = (item.get("keynote") or "").strip()
        if not txt:
            continue
        conn.execute("UPDATE papers SET keynote=? WHERE id=?", (txt, int(pid)))
        audit(conn, "agent", "keynote.paper", "paper", pid, {"keynote": txt[:80]})
        n += 1
    conn.commit()
    print(f"[keynote] 入库 {n} 条（papers.keynote，域 {domain}）")


def cmd_list_pending(args):
    conn = connect()
    init_db(conn)
    q = """SELECT p.id, p.title FROM papers p JOIN paper_domains pd ON pd.paper_id=p.id
           WHERE pd.domain_id=? AND (p.keynote IS NULL OR p.keynote='')
           ORDER BY p.cited_by_count DESC"""
    rows = conn.execute(q + (" LIMIT 20" if not args.all else ""), (args.domain,)).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) n FROM papers p JOIN paper_domains pd ON pd.paper_id=p.id "
        "WHERE pd.domain_id=? AND (p.keynote IS NULL OR p.keynote='')", (args.domain,)).fetchone()["n"]
    print(f"[keynote] 域 {args.domain} 待补 keynote {total} 篇；示例：")
    for r in rows:
        print(f"  {r['id']}  {r['title'][:70]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="一句话关键词入库管理")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("apply-papers"); p1.add_argument("json")
    p2 = sub.add_parser("list-pending"); p2.add_argument("--domain", default="neuroling"); p2.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.cmd == "apply-papers":
        cmd_apply_papers(args)
    elif args.cmd == "list-pending":
        cmd_list_pending(args)
    else:
        ap.print_help()
