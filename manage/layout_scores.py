"""布局评分管理：LLM 的 方向两两关联(dir_sim) / 论文×方向关联(paper_aff) 评分的锚定校验、入库与审阅。

子命令：
  apply  <json>                批量入库（校验 domain/方向/论文存在性；去重）
  list   [--pending]           清单
  review <id> approve|reject --by <署名>   人工审阅（批准后供 analyze/layout.py 使用）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, init_db

OK_DIR = ("dir_sim", "paper_aff")


def _domain_clusters(conn, domain):
    return {r["id"] for r in conn.execute(
        "SELECT id FROM clusters WHERE domain_id=? AND batch_id="
        "(SELECT MAX(batch_id) FROM clusters WHERE domain_id=?)", (domain, domain))}


def cmd_apply(args):
    doc = json.loads(Path(args.json).read_text(encoding="utf-8"))
    conn = connect()
    init_db(conn)
    domain = doc["domain"]
    if not conn.execute("SELECT id FROM domains WHERE id=?", (domain,)).fetchone():
        raise SystemExit(f"域不存在：{domain}")
    clusters = _domain_clusters(conn, domain)
    paper_ids = {r["id"] for r in conn.execute(
        "SELECT p.id FROM papers p JOIN paper_domains pd ON pd.paper_id=p.id WHERE pd.domain_id=?",
        (domain,))}
    model = doc.get("model", "agent")
    pver = doc.get("prompt_ver", "-")
    ok, fail = 0, []
    for st in doc.get("dir_sim", []):
        a, b = str(st["from_cluster"]), str(st["to_cluster"])
        if int(a) not in clusters or int(b) not in clusters:
            fail.append(f"dir_sim {a}~{b}: 方向不在该域最新批次")
            continue
        _ins(conn, domain, "dir_sim", a, b, st, model, pver)
        ok += 1
    for st in doc.get("paper_aff", []):
        pid, cid = str(st["paper_id"]), str(st.get("cluster_id", doc.get("cluster_id")))
        if int(pid) not in paper_ids:
            fail.append(f"paper_aff {pid}: 论文不在该域")
            continue
        if cid is None or int(cid) not in clusters:
            fail.append(f"paper_aff {pid}: cluster {cid} 不在该域最新批次")
            continue
        _ins(conn, domain, "paper_aff", pid, cid, st, model, pver)
        ok += 1
    conn.commit()
    print(f"[layout-scores] 入库 {ok} 条，拦截 {len(fail)} 条")
    for f in fail:
        print(f"  ⚠ {f}")
    if fail:
        raise SystemExit(1)


def _ins(conn, domain, stype, fid, tid, st, model, pver):
    conn.execute(
        """INSERT INTO layout_scores(domain_id, score_type, from_id, to_id, value, reason, model, prompt_ver)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(domain_id, score_type, from_id, to_id) DO UPDATE
             SET value=excluded.value, reason=excluded.reason, model=excluded.model,
                 prompt_ver=excluded.prompt_ver, status='pending'""",
        (domain, stype, fid, tid, float(st["value"]), st.get("reason"), model, pver))
    audit(conn, "agent", f"layout_score.{stype}", "cluster" if stype == "dir_sim" else "paper",
          fid if stype == "dir_sim" else tid, {"score": st["value"]})


def cmd_list(args):
    conn = connect()
    init_db(conn)
    q = "SELECT * FROM layout_scores"
    rows = conn.execute(q + (" WHERE status='pending'" if args.pending else "") + " ORDER BY id",
                        () if not args.pending else ()).fetchall()
    for r in rows:
        print(f"#{r['id']:<4} [{r['status']:<8}] {r['score_type']:<9} {r['from_id']}→{r['to_id']} "
              f"v={r['value']:.2f} {r['created_at']}")
    if not rows:
        print("（无评分）")


def cmd_review(args):
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM layout_scores WHERE id=?", (args.id,)).fetchone()
    if not row:
        raise SystemExit(f"评分不存在：{args.id}")
    if row["status"] != "pending":
        raise SystemExit(f"已裁决（{row['status']}）")
    conn.execute("UPDATE layout_scores SET status=?, model=model||'' WHERE id=?",
                 ("approved" if args.action == "approve" else "rejected", args.id))
    audit(conn, f"user:{args.by}", f"layout_score.{args.action}", "layout_scores", args.id, None)
    conn.commit()
    print(f"[layout-scores] #{args.id} → {args.action}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="布局评分管理")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("apply")
    p1.add_argument("json")
    p2 = sub.add_parser("list")
    p2.add_argument("--pending", action="store_true")
    p3 = sub.add_parser("review")
    p3.add_argument("id", type=int)
    p3.add_argument("action", choices=["approve", "reject"])
    p3.add_argument("--by", required=True)
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        raise SystemExit(1)
    {"apply": cmd_apply, "list": cmd_list, "review": cmd_review}[args.cmd](args)
