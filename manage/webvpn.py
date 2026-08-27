"""机构 webvpn 导入管理：批次清单 / 导入（封装 ingest/webvpn_import.py）。

子命令：
  import  <file> --source scopus|ris --domain <域> [--query ...] [--dry-run]
  list    [--domain <域>]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import connect, init_db


def cmd_list(args):
    conn = connect()
    init_db(conn)
    q = """SELECT i.id, i.domain_id, i.source, i.file_name, i.query, i.n_records, i.n_new, i.n_dup,
                  i.imported_at FROM webvpn_imports i"""
    rows = conn.execute(q + (" WHERE i.domain_id=?" if args.domain else "") + " ORDER BY i.id DESC",
                        (args.domain,) if args.domain else ()).fetchall()
    if not rows:
        print("（无 webvpn 导入批次）")
        return
    for r in rows:
        print(f"#{r['id']:<4} {r['domain_id']:<10} {r['source']:<8} {r['file_name'] or '-':<36} "
              f"记录{r['n_records']} 新增{r['n_new']} 去重{r['n_dup']} {r['imported_at']}")
        if r["query"]:
            print(f"      检索式：{r['query']}")


def cmd_import(args):
    from ingest.webvpn_import import ingest_file
    ingest_file(Path(args.file), args.source, args.domain, args.query, args.dry_run)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="机构 webvpn 导入管理")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("import", help="导入题录文件")
    p1.add_argument("file")
    p1.add_argument("--source", required=True, choices=["scopus", "cnki", "wanfang"])
    p1.add_argument("--domain", required=True)
    p1.add_argument("--query", default="")
    p1.add_argument("--dry-run", action="store_true")
    p2 = sub.add_parser("list", help="批次清单")
    p2.add_argument("--domain")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        raise SystemExit(1)
    {"import": cmd_import, "list": cmd_list}[args.cmd](args)
