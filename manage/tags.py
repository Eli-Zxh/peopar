"""研究者标签管理：受控词表（tag_vocab）+ 研究者标签（researcher_tags）。

子命令：
  seed <json>                  初始词表入库（approved）
  propose-vocab <json>         新词提案（status=proposed → review 裁决）
  review-vocab <id> approve|reject --by <署名>   词表裁决（approve 即 approved）
  suggest <json>               研究者标签建议（deepdive 产出 → researcher_tags pending）
  list-vocab [--dim X]         词表
  list-tags <author_id>        某研究者标签
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, init_db


def _upsert_vocab(conn, dim, tag, status, by=None):
    row = conn.execute("SELECT id FROM tag_vocab WHERE tag=?", (tag,)).fetchone()
    if row:
        conn.execute("UPDATE tag_vocab SET dim=?, status=? WHERE id=?", (dim, status, row["id"]))
        return row["id"]
    cur = conn.execute(
        "INSERT INTO tag_vocab(dim, tag, status, proposed_by) VALUES(?,?,?,?)",
        (dim, tag, status, by))
    return cur.lastrowid


def cmd_seed(args):
    doc = json.loads(Path(args.json).read_text(encoding="utf-8"))
    conn = connect()
    init_db(conn)
    n = 0
    for t in doc["tags"]:
        _upsert_vocab(conn, t["dim"], t["tag"], "approved")
        n += 1
    conn.commit()
    print(f"[tags] seed {n} 词（approved）")


def cmd_propose_vocab(args):
    doc = json.loads(Path(args.json).read_text(encoding="utf-8"))
    conn = connect()
    init_db(conn)
    n = 0
    for t in doc["tags"]:
        _upsert_vocab(conn, t["dim"], t["tag"], "proposed", args.by)
        n += 1
    conn.commit()
    print(f"[tags] 提案 {n} 新词 → review 裁决")


def cmd_review_vocab(args):
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM tag_vocab WHERE id=?", (args.id,)).fetchone()
    if not row:
        raise SystemExit(f"词不存在：{args.id}")
    status = "approved" if args.action == "approve" else "rejected"
    conn.execute("UPDATE tag_vocab SET status=? WHERE id=?", (status, args.id))
    audit(conn, f"user:{args.by}", f"tag_vocab.{args.action}", "tag_vocab", args.id,
          {"tag": row["tag"]})
    conn.commit()
    print(f"[tags] 词 #{args.id}「{row['tag']}」→ {status}")


def cmd_suggest(args):
    """研究者标签建议：{author_tags: [{author_id, tag, basis}], new_tags: [{dim, tag}]}"""
    doc = json.loads(Path(args.json).read_text(encoding="utf-8"))
    conn = connect()
    init_db(conn)
    for nt in doc.get("new_tags", []):
        _upsert_vocab(conn, nt["dim"], nt["tag"], "proposed", "agent")
    n = 0
    for st in doc.get("author_tags", []):
        a = conn.execute("SELECT id FROM authors WHERE id=?", (st["author_id"],)).fetchone()
        t = conn.execute("SELECT id, status FROM tag_vocab WHERE tag=?", (st["tag"],)).fetchone()
        if not a:
            print(f"  ⚠ 作者不存在 {st['author_id']}")
            continue
        if not t:
            print(f"  ⚠ 词不在词表（先 propose-vocab）：{st['tag']}")
            continue
        if t["status"] != "approved":
            print(f"  ⚠ 词未批准：{st['tag']}")
            continue
        conn.execute(
            """INSERT INTO researcher_tags(author_id, tag_id, status, source, basis, created_by)
               VALUES(?,?,'pending','deepdive',?, 'agent')
               ON CONFLICT(author_id, tag_id) DO NOTHING""",
            (a["id"], t["id"], st.get("basis", "")))
        n += 1
    audit(conn, "agent", "tag.suggest", "tags", None, {"n": n})
    conn.commit()
    print(f"[tags] 研究者标签建议 {n} 条（pending，待人工批准）")


def cmd_list_vocab(args):
    conn = connect()
    init_db(conn)
    q = "SELECT id, dim, tag, status FROM tag_vocab"
    rows = conn.execute(q + (" WHERE dim=?" if args.dim else "") + " ORDER BY dim, tag",
                        (args.dim,) if args.dim else ()).fetchall()
    for r in rows:
        print(f"#{r['id']:<4} [{r['status']:<8}] {r['dim']:<14} {r['tag']}")


def cmd_list_tags(args):
    conn = connect()
    init_db(conn)
    rows = conn.execute(
        """SELECT v.tag, v.dim, t.status, t.basis FROM researcher_tags t
           JOIN tag_vocab v ON v.id=t.tag_id WHERE t.author_id=?
           ORDER BY t.status, v.dim""", (args.author_id,)).fetchall()
    if not rows:
        print(f"（{args.author_id} 无标签）")
        return
    for r in rows:
        print(f"  [{r['status']:<8}] {r['dim']:<14} {r['tag']}  {r['basis'] or ''}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="研究者标签管理")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("seed"); p1.add_argument("json")
    p2 = sub.add_parser("propose-vocab"); p2.add_argument("json"); p2.add_argument("--by", default="user")
    p3 = sub.add_parser("review-vocab"); p3.add_argument("id", type=int)
    p3.add_argument("action", choices=["approve", "reject"]); p3.add_argument("--by", required=True)
    p4 = sub.add_parser("suggest"); p4.add_argument("json")
    p5 = sub.add_parser("list-vocab"); p5.add_argument("--dim")
    p6 = sub.add_parser("list-tags"); p6.add_argument("author_id")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        raise SystemExit(1)
    {"seed": cmd_seed, "propose-vocab": cmd_propose_vocab, "review-vocab": cmd_review_vocab,
     "suggest": cmd_suggest, "list-vocab": cmd_list_vocab, "list-tags": cmd_list_tags}[args.cmd](args)
