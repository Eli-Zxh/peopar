"""机构官网信息管理：AI 助手按 skill 抓取核心作者任职机构主页后，经本 CLI 写入待校验队列。

原则：
- 官网抓取是「可联系研究者」的必要数据（核心作者从任职机构官网校验职位/邮箱/方向）；
- 抓取内容一律 source_tag='web' 且 verified=0，由人工在校对队列确认后生效；
- 全程审计留痕（actor=user:<署名>）。

子命令：
  add     <author_id> --institution <机构> [--role <职位>] [--url <来源页>] [--email <邮箱>] --by <署名>
  verify  <id> --by <署名>           人工确认机构信息
  dismiss <id> --by <署名>           驳回
  queue   [--limit N]                待校验队列（web 来源 + 未确认）
  list    <author_id>                某研究者的全部履历
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, init_db


def _add(args):
    conn = connect()
    init_db(conn)
    a = conn.execute("SELECT id, name_display FROM authors WHERE id=?", (args.author_id,)).fetchone()
    if not a:
        raise SystemExit(f"作者不存在：{args.author_id}")
    if not args.institution:
        raise SystemExit("--institution 必填")
    cur = conn.execute(
        """INSERT INTO affiliations(author_id, institution, institution_norm, source_tag, source_url,
           note, confidence, verified)
           VALUES(?,?,?, 'web', ?, ?, 1.0, 0)""",
        (args.author_id, args.institution, args.institution,
         args.url, json.dumps({"role": args.role, "email": args.email}, ensure_ascii=False)))
    aid = cur.lastrowid
    audit(conn, f"user:{args.by}", "affiliation.web_add", "author", args.author_id,
          {"aff_id": aid, "institution": args.institution, "role": args.role,
           "email": args.email, "url": args.url})
    conn.commit()
    print(f"[aff] 已写入待校验履历 #{aid}：{a['name_display']} @ {args.institution}"
          + (f"（{args.role}）" if args.role else ""))


def _verify(args):
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT id, author_id FROM affiliations WHERE id=?", (args.id,)).fetchone()
    if not row:
        raise SystemExit(f"履历不存在：{args.id}")
    conn.execute("UPDATE affiliations SET verified=1, source_url=COALESCE(source_url, ?) WHERE id=?",
                 (args.url, args.id))
    audit(conn, f"user:{args.by}", "affiliation.verify", "author", row["author_id"], {"aff_id": args.id})
    conn.commit()
    print(f"[aff] 已确认履历 #{args.id}")


def _dismiss(args):
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT id, author_id FROM affiliations WHERE id=?", (args.id,)).fetchone()
    if not row:
        raise SystemExit(f"履历不存在：{args.id}")
    conn.execute("UPDATE affiliations SET verified=-1 WHERE id=?", (args.id,))
    audit(conn, f"user:{args.by}", "affiliation.dismiss", "author", row["author_id"], {"aff_id": args.id})
    conn.commit()
    print(f"[aff] 已驳回履历 #{args.id}")


def _queue(args):
    conn = connect()
    init_db(conn)
    rows = conn.execute(
        """SELECT af.id, af.author_id, a.name_display, a.name_zh, af.institution, af.source_tag,
                  af.source_url, af.note, af.confidence
           FROM affiliations af JOIN authors a ON a.id=af.author_id
           WHERE af.source_tag='web' AND af.verified=0 ORDER BY af.id LIMIT ?""",
        (args.limit,)).fetchall()
    if not rows:
        print("（无待校验的机构官网信息）")
        return
    for r in rows:
        note = json.loads(r["note"]) if r["note"] else {}
        print(f"#{r['id']:<5} {r['name_display']}（{r['name_zh'] or '-'}）[{r['author_id']}]")
        print(f"      机构：{r['institution']}  职位：{note.get('role') or '-'}  邮箱：{note.get('email') or '-'}")
        if r["source_url"]:
            print(f"      来源：{r['source_url']}")


def _list(args):
    conn = connect()
    init_db(conn)
    rows = conn.execute(
        "SELECT id, institution, start_year, end_year, source_tag, source_url, verified, note "
        "FROM affiliations WHERE author_id=? ORDER BY start_year, id", (args.author_id,)).fetchall()
    if not rows:
        print("（无履历）")
        return
    for r in rows:
        note = json.loads(r["note"]) if r["note"] else {}
        print(f"#{r['id']:<5} {r['institution']}（{r['start_year'] or '?'}–{r['end_year'] or ''}）"
              f"[{r['source_tag']}] {'✅' if r['verified'] == 1 else ('❌' if r['verified'] == -1 else '待校验')}"
              + (f" {note.get('role') or ''}" if note else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="机构官网信息管理")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("add")
    p1.add_argument("author_id")
    p1.add_argument("--institution", required=True)
    p1.add_argument("--role")
    p1.add_argument("--url")
    p1.add_argument("--email")
    p1.add_argument("--by", required=True)
    p2 = sub.add_parser("verify")
    p2.add_argument("id", type=int)
    p2.add_argument("--by", required=True)
    p2.add_argument("--url")
    p3 = sub.add_parser("dismiss")
    p3.add_argument("id", type=int)
    p3.add_argument("--by", required=True)
    p4 = sub.add_parser("queue")
    p4.add_argument("--limit", type=int, default=100)
    p5 = sub.add_parser("list")
    p5.add_argument("author_id")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        raise SystemExit(1)
    {"add": _add, "verify": _verify, "dismiss": _dismiss, "queue": _queue, "list": _list}[args.cmd](args)
