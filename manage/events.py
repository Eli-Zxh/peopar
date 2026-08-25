"""造假事件管理：登记、自动标记、人工定性、修订报告。全程审计留痕。

状态机：suspected → verifying → confirmed / dismissed
人员分级：L0 确认造假（必须人工定性）；L1 风险提示（自动，仅共著/引用关联，绝非定性）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, init_db


def apply(json_path: str):
    """登记事件 + 自动论文级标记 + L1 风险提示 + L0 候选(待确认)。"""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    conn = connect()
    init_db(conn)
    domain_id = data["domain_id"]
    # 1) 事件
    row = conn.execute("SELECT id FROM fraud_events WHERE slug=?", (data["slug"],)).fetchone()
    if row:
        event_id = row["id"]
        conn.execute(
            "UPDATE fraud_events SET title=?, description=?, status=?, source_urls=? WHERE id=?",
            (data["title"], data.get("description", ""), data.get("status", "verifying"),
             json.dumps(data.get("source_urls", []), ensure_ascii=False), event_id))
    else:
        cur = conn.execute(
            """INSERT INTO fraud_events(slug, title, description, domain_id, status, source_urls)
               VALUES(?,?,?,?,?,?)""",
            (data["slug"], data["title"], data.get("description", ""), domain_id,
             data.get("status", "verifying"),
             json.dumps(data.get("source_urls", []), ensure_ascii=False)))
        event_id = cur.lastrowid
    audit(conn, "system", "event.register", "fraud_event", event_id,
          {"slug": data["slug"], "domain": domain_id})
    # 2) 论文级标记：仅当事作者的权威撤稿/更正/关注论文挂接到事件
    cand_author_ids = []
    for cand in data.get("l0_candidates", []):
        ar = conn.execute("SELECT id FROM authors WHERE name_display=?", (cand["name_query"],)).fetchone()
        if ar:
            cand_author_ids.append(ar["id"])
    rows = []
    if cand_author_ids:
        ph = ",".join("?" * len(cand_author_ids))
        rows = conn.execute(
            f"""SELECT DISTINCT p.id, p.pmid, p.title, p.retraction_status FROM papers p
               JOIN paper_authors pa ON pa.paper_id=p.id
               WHERE pa.author_id IN ({ph})
                 AND p.retraction_status IN ('retracted','corrected','concern')""",
            cand_author_ids).fetchall()
    type_map = {"retracted": "retraction", "corrected": "correction", "concern": "expression_of_concern"}
    n_flag = 0
    for r in rows:
        ex = conn.execute(
            "SELECT id FROM paper_flags WHERE paper_id=? AND event_id=?", (r["id"], event_id)).fetchone()
        if ex:
            continue
        conn.execute(
            """INSERT INTO paper_flags(paper_id, event_id, flag_type, note, source_url, created_by)
               VALUES(?,?,?,?,?, 'system')""",
            (r["id"], event_id, type_map[r["retraction_status"]],
             f"PubMed 权威标注：{r['retraction_status']}",
             f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r["pmid"] else None))
        n_flag += 1
    # 3) L0 候选（待人工确认，绝不自动定性）
    for cand in data.get("l0_candidates", []):
        ar = conn.execute("SELECT id FROM authors WHERE name_display=?", (cand["name_query"],)).fetchone()
        if not ar:
            print(f"[warn] L0 候选未找到作者：{cand['name_query']}")
            continue
        ex = conn.execute(
            "SELECT id FROM author_flags WHERE author_id=? AND event_id=? AND level='L0'",
            (ar["id"], event_id)).fetchone()
        if not ex:
            conn.execute(
                """INSERT INTO author_flags(author_id, event_id, level, basis, status, created_by)
                   VALUES(?,?, 'L0', ?, 'pending', 'system')""",
                (ar["id"], event_id, cand.get("basis", "")))
            audit(conn, "system", "flag.l0_pending", "author", ar["id"],
                  {"event": data["slug"], "basis": cand.get("basis", "")})
    # 4) L1 风险提示：被标记论文的共同作者（共同作者≠共谋，仅提示）
    flagged_papers = [r["paper_id"] for r in conn.execute(
        "SELECT DISTINCT paper_id FROM paper_flags WHERE event_id=?", (event_id,))]
    l0_authors = {r["author_id"] for r in conn.execute(
        "SELECT author_id FROM author_flags WHERE event_id=? AND level='L0'", (event_id,))}
    n_l1 = 0
    for pid in flagged_papers:
        for r in conn.execute("SELECT author_id FROM paper_authors WHERE paper_id=?", (pid,)):
            aid = r["author_id"]
            if aid in l0_authors:
                continue
            ex = conn.execute(
                "SELECT id FROM author_flags WHERE author_id=? AND event_id=? AND level='L1'",
                (aid, event_id)).fetchone()
            if ex:
                continue
            conn.execute(
                """INSERT INTO author_flags(author_id, event_id, level, basis, status, created_by)
                   VALUES(?,?, 'L1', ?, 'confirmed', 'system')""",
                (aid, event_id,
                 f"与事件标记论文({pid})存在共同作者关系——仅风险提示，绝非定性"))
            n_l1 += 1
    audit(conn, "system", "event.autoflag", "fraud_event", event_id,
          {"paper_flags": n_flag, "l1_hints": n_l1})
    conn.commit()
    print(f"[event] {data['slug']}: 论文标记 +{n_flag}，L0 候选待确认，L1 风险提示 +{n_l1}")


def confirm_l0(slug: str, name_query: str, confirmed_by: str, basis: str = ""):
    """人工定性 L0（必须人工，留痕）。"""
    conn = connect()
    ev = conn.execute("SELECT id FROM fraud_events WHERE slug=?", (slug,)).fetchone()
    if not ev:
        raise SystemExit(f"事件不存在：{slug}")
    ar = conn.execute("SELECT id, name_display FROM authors WHERE name_display LIKE ?",
                      (f"%{name_query}%",)).fetchone()
    if not ar:
        raise SystemExit(f"作者不存在：{name_query}")
    row = conn.execute(
        "SELECT id, status FROM author_flags WHERE author_id=? AND event_id=? AND level='L0'",
        (ar["id"], ev["id"])).fetchone()
    if not row:
        raise SystemExit("该作者在此事件下无 L0 候选记录")
    conn.execute(
        "UPDATE author_flags SET status='confirmed', confirmed_by=?, basis=basis||? WHERE id=?",
        (confirmed_by, f" ｜人工定性：{basis}" if basis else "", row["id"]))
    audit(conn, f"user:{confirmed_by}", "flag.l0_confirm", "author", ar["id"],
          {"event": slug, "basis": basis})
    conn.commit()
    print(f"[L0] 已人工确认 {ar['name_display']} 为事件 {slug} 的确认造假者（留痕完成）")


def confirm_event(slug: str, confirmed_by: str):
    """事件核实完成（人工），生成受影响修订报告。"""
    conn = connect()
    ev = conn.execute("SELECT * FROM fraud_events WHERE slug=?", (slug,)).fetchone()
    if not ev:
        raise SystemExit(f"事件不存在：{slug}")
    conn.execute(
        "UPDATE fraud_events SET status='confirmed', confirmed_at=datetime('now'), confirmed_by=? WHERE id=?",
        (confirmed_by, ev["id"]))
    audit(conn, f"user:{confirmed_by}", "event.confirm", "fraud_event", ev["id"], {"slug": slug})
    report = build_report(conn, ev)
    out = Path(__file__).resolve().parent.parent / "data" / f"revision_{slug}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.commit()
    print(f"[event] {slug} 已确认；修订报告 → {out.name}")


def build_report(conn, ev):
    """修订建议报告：受影响论文、风险作者、施引文献（知识影响面）。"""
    flags = conn.execute(
        """SELECT pf.flag_type, pf.note, pf.source_url, p.title, p.pmid, p.year
           FROM paper_flags pf JOIN papers p ON p.id=pf.paper_id WHERE pf.event_id=?""",
        (ev["id"],)).fetchall()
    authors = conn.execute(
        """SELECT af.level, af.status, af.basis, a.name_display, a.id,
                  (SELECT COUNT(*) FROM paper_authors pa WHERE pa.author_id=a.id) AS papers
           FROM author_flags af JOIN authors a ON a.id=af.author_id WHERE af.event_id=?
           ORDER BY af.level""", (ev["id"],)).fetchall()
    cited = conn.execute(
        """SELECT DISTINCT p.id, p.title, p.pmid, p.year FROM citations c
           JOIN papers p ON p.id=c.citing_id
           WHERE c.cited_id IN (SELECT paper_id FROM paper_flags WHERE event_id=?)
           ORDER BY p.year DESC LIMIT 200""", (ev["id"],)).fetchall()
    return {
        "event": {"slug": ev["slug"], "title": ev["title"], "status": "confirmed"},
        "flagged_papers": [dict(f) for f in flags],
        "authors": [dict(a) for a in authors],
        "affected_citing_papers": [dict(c) for c in cited],
        "note": "受影响方向快照的修订需在管理台人工确认；共同作者仅 L1 提示，不构成定性。",
    }


def list_events():
    conn = connect()
    init_db(conn)
    for ev in conn.execute("SELECT * FROM fraud_events ORDER BY id"):
        nf = conn.execute("SELECT COUNT(*) n FROM paper_flags WHERE event_id=?", (ev["id"],)).fetchone()["n"]
        na = conn.execute(
            "SELECT level, COUNT(*) n FROM author_flags WHERE event_id=? GROUP BY level", (ev["id"],)).fetchall()
        print(f"#{ev['id']} [{ev['status']}] {ev['slug']} — {ev['title']}  论文标记 {nf}，人员 " +
              "，".join(f"{r['level']}×{r['n']}" for r in na))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="造假事件管理")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("apply", help="登记事件并自动标记")
    p1.add_argument("json")
    p2 = sub.add_parser("confirm-l0", help="人工定性 L0")
    p2.add_argument("slug")
    p2.add_argument("name")
    p2.add_argument("--by", required=True)
    p2.add_argument("--basis", default="")
    p3 = sub.add_parser("confirm-event", help="人工确认事件并生成修订报告")
    p3.add_argument("slug")
    p3.add_argument("--by", required=True)
    p4 = sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "apply":
        apply(args.json)
    elif args.cmd == "confirm-l0":
        confirm_l0(args.slug, args.name, args.by, args.basis)
    elif args.cmd == "confirm-event":
        confirm_event(args.slug, args.by)
    elif args.cmd == "list":
        list_events()
