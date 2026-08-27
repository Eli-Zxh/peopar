"""LLM 判断裁决队列：噪声簇 / 方向并拆 / 别名候选。

机制：LLM（skill 会话）提出 → 人工裁决 → 留痕。任何 accept 都不修改簇成员历史；
噪声簇 accept 仅置 clusters.display='excluded'（展示层折叠）。

子命令：
  propose <json>                    提案入库（批量；同 jtype+entity 有 pending 时拒收）
  list    [--status pending|accepted|rejected]
  decide  <id> accept|reject --by <署名> [--note ...]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, init_db


def cmd_propose(args):
    doc = json.loads(Path(args.json).read_text(encoding="utf-8"))
    conn = connect()
    init_db(conn)
    ok, fail = 0, []
    for j in doc.get("judgments", []):
        jtype, etype, eid = j["jtype"], j["entity_type"], str(j["entity_id"])
        dup = conn.execute(
            """SELECT id FROM judgments WHERE jtype=? AND entity_type=? AND entity_id=?
               AND status='pending'""", (jtype, etype, eid)).fetchone()
        if dup:
            fail.append({"id": j.get("id"), "reason": f"同 {jtype}@{eid} 已有 pending 提案 #{dup['id']}"})
            continue
        cur = conn.execute(
            "INSERT INTO judgments(jtype, entity_type, entity_id, proposal) VALUES(?,?,?,?)",
            (jtype, etype, eid, json.dumps(j.get("proposal", {}), ensure_ascii=False)))
        audit(conn, "agent", "judgment.propose", etype, eid,
              {"judgment_id": cur.lastrowid, "jtype": jtype})
        ok += 1
    conn.commit()
    print(f"[judgment] 入库 {ok} 条，拦截 {len(fail)} 条")
    for f in fail:
        print(f"  ⚠ {f['reason']}")


def cmd_list(args):
    conn = connect()
    init_db(conn)
    q = "SELECT * FROM judgments"
    rows = conn.execute(q + (" WHERE status=?" if args.status else "") + " ORDER BY id",
                        (args.status,) if args.status else ()).fetchall()
    if not rows:
        print("（无判断提案）")
        return
    for r in rows:
        print(f"#{r['id']:<4} [{r['status']:<8}] {r['jtype']:<18} {r['entity_type']}:{r['entity_id']}"
              f"  {r['created_at']}")
        p = json.loads(r["proposal"] or "{}")
        print(f"       {str(p)[:120]}")


def cmd_decide(args):
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM judgments WHERE id=?", (args.id,)).fetchone()
    if not row:
        raise SystemExit(f"提案不存在：{args.id}")
    if row["status"] != "pending":
        raise SystemExit(f"提案已裁决（{row['status']}）")
    actor = f"user:{args.by}"
    status = "accepted" if args.action == "accept" else "rejected"
    if args.action == "accept":
        # 生效动作（均不触碰簇成员历史）
        if row["jtype"] == "noise_cluster" and row["entity_type"] == "cluster":
            conn.execute("UPDATE clusters SET display='excluded' WHERE id=?", (row["entity_id"],))
        elif row["jtype"] == "alias_candidate" and row["entity_type"] == "author":
            p = json.loads(row["proposal"] or "{}")
            alias = p.get("alias")
            if alias:
                conn.execute(
                    """INSERT OR IGNORE INTO author_aliases(author_id, alias, alias_type, source,
                       confidence, verified) VALUES(?,?,?, 'llm', 0.6, 0)""",
                    (row["entity_id"], alias, p.get("alias_type", "hanzi")))
        # direction_merge/split：仅留痕，作为后续合成措辞依据
    conn.execute(
        "UPDATE judgments SET status=?, decided_by=?, decided_at=datetime('now'), decision_note=? WHERE id=?",
        (status, actor, args.note, args.id))
    # 旧 accepted 判决被覆盖（历史不删）
    conn.execute(
        """UPDATE judgments SET status='superseded' WHERE jtype=? AND entity_type=? AND entity_id=?
           AND status='accepted' AND id!=?""",
        (row["jtype"], row["entity_type"], row["entity_id"], args.id))
    audit(conn, actor, f"judgment.{args.action}", row["entity_type"], row["entity_id"],
          {"judgment_id": args.id, "jtype": row["jtype"], "note": args.note})
    conn.commit()
    print(f"[judgment] #{args.id} {row['jtype']}@{row['entity_id']} → {args.action}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="LLM 判断裁决队列")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("propose")
    p1.add_argument("json")
    p2 = sub.add_parser("list")
    p2.add_argument("--status", choices=["pending", "accepted", "rejected"])
    p3 = sub.add_parser("decide")
    p3.add_argument("id", type=int)
    p3.add_argument("action", choices=["accept", "reject"])
    p3.add_argument("--by", required=True)
    p3.add_argument("--note")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        raise SystemExit(1)
    {"propose": cmd_propose, "list": cmd_list, "decide": cmd_decide}[args.cmd](args)
