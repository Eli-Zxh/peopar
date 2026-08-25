"""百官行述 · 本地应用（零依赖：标准库 http.server + sqlite3）。

启动：python3 app.py  →  http://127.0.0.1:8765
"""
import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest.common import ROOT, audit, connect, init_db

PORT = 8765
WEB = ROOT / "web"
STATIC = ROOT / "static"


def jbody(handler) -> dict:
    ln = int(handler.headers.get("Content-Length") or 0)
    if not ln:
        return {}
    return json.loads(handler.rfile.read(ln).decode("utf-8") or "{}")


def rows_to_list(rows):
    return [dict(r) for r in rows]


class API:
    """查询与操作入口，全部返回可 JSON 序列化对象。"""

    @staticmethod
    def domains(conn):
        out = []
        for d in conn.execute("SELECT * FROM domains ORDER BY id"):
            n_papers = conn.execute(
                "SELECT COUNT(*) n FROM paper_domains WHERE domain_id=?", (d["id"],)).fetchone()["n"]
            n_authors = conn.execute(
                """SELECT COUNT(DISTINCT pa.author_id) n FROM paper_authors pa
                   JOIN paper_domains pd ON pd.paper_id=pa.paper_id WHERE pd.domain_id=?""",
                (d["id"],)).fetchone()["n"]
            graph = ROOT / "data" / f"graph_{d['id']}.json"
            out.append({**dict(d), "papers": n_papers, "authors": n_authors,
                        "graph_ready": graph.exists()})
        return out

    @staticmethod
    def graph(conn, domain):
        path = ROOT / "data" / f"graph_{domain}.json"
        if not path.exists():
            return {"error": f"图谱未生成：请先运行 python3 analyze/graph.py {domain}"}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def author(conn, aid):
        a = conn.execute("SELECT * FROM authors WHERE id=?", (aid,)).fetchone()
        if not a:
            return {"error": "作者不存在"}
        info = dict(a)
        info["aliases"] = rows_to_list(conn.execute(
            "SELECT * FROM author_aliases WHERE author_id=? ORDER BY alias_type, alias", (aid,)))
        info["affiliations"] = rows_to_list(conn.execute(
            "SELECT * FROM affiliations WHERE author_id=? ORDER BY start_year", (aid,)))
        info["flags"] = rows_to_list(conn.execute(
            """SELECT af.*, fe.slug, fe.title AS event_title, fe.status AS event_status
               FROM author_flags af LEFT JOIN fraud_events fe ON fe.id=af.event_id
               WHERE af.author_id=?""", (aid,)))
        info["clusters"] = rows_to_list(conn.execute(
            """SELECT c.id, c.label, c.name, c.batch_id, ac.weight FROM author_clusters ac
               JOIN clusters c ON c.id=ac.cluster_id WHERE ac.author_id=?""", (aid,)))
        info["papers"] = rows_to_list(conn.execute(
            """SELECT p.id, p.title, p.year, p.journal, p.pmid, p.doi, p.cited_by_count,
                      p.retraction_status, pa.position,
                      (SELECT COUNT(*) FROM paper_flags pf WHERE pf.paper_id=p.id) AS n_flags
               FROM paper_authors pa JOIN papers p ON p.id=pa.paper_id
               WHERE pa.author_id=? ORDER BY p.year DESC, pa.position""", (aid,)))
        info["collaborators"] = rows_to_list(conn.execute(
            """SELECT b.id, b.name_display, b.name_zh, b.tier, COUNT(*) AS co_papers
               FROM paper_authors pa1
               JOIN paper_authors pa2 ON pa2.paper_id=pa1.paper_id AND pa2.author_id!=pa1.author_id
               JOIN authors b ON b.id=pa2.author_id
               WHERE pa1.author_id=? GROUP BY b.id ORDER BY co_papers DESC LIMIT 30""", (aid,)))
        info["audit"] = rows_to_list(conn.execute(
            "SELECT ts, actor, action, detail FROM audit_log WHERE entity_type='author' AND entity_id=? "
            "ORDER BY ts DESC LIMIT 50", (aid,)))
        return info

    @staticmethod
    def event(conn, eid):
        ev = conn.execute("SELECT * FROM fraud_events WHERE id=?", (eid,)).fetchone()
        if not ev:
            return {"error": "事件不存在"}
        info = dict(ev)
        info["source_urls"] = json.loads(ev["source_urls"] or "[]")
        info["paper_flags"] = rows_to_list(conn.execute(
            """SELECT pf.*, p.title, p.year, p.pmid FROM paper_flags pf
               JOIN papers p ON p.id=pf.paper_id WHERE pf.event_id=?""", (eid,)))
        info["author_flags"] = rows_to_list(conn.execute(
            """SELECT af.*, a.name_display, a.name_zh, a.tier FROM author_flags af
               JOIN authors a ON a.id=af.author_id WHERE af.event_id=? ORDER BY af.level""", (eid,)))
        info["audit"] = rows_to_list(conn.execute(
            "SELECT ts, actor, action, detail FROM audit_log WHERE entity_type='fraud_event' AND entity_id=? "
            "ORDER BY ts", (eid,)))
        slug = ev["slug"]
        report = ROOT / "data" / f"revision_{slug}.json"
        info["report"] = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
        return info

    @staticmethod
    def events(conn):
        out = []
        for ev in conn.execute("SELECT * FROM fraud_events ORDER BY id"):
            d = dict(ev)
            d["source_urls"] = json.loads(ev["source_urls"] or "[]")
            d["n_paper_flags"] = conn.execute(
                "SELECT COUNT(*) n FROM paper_flags WHERE event_id=?", (ev["id"],)).fetchone()["n"]
            d["n_l0"] = conn.execute(
                "SELECT COUNT(*) n FROM author_flags WHERE event_id=? AND level='L0'", (ev["id"],)).fetchone()["n"]
            d["n_l1"] = conn.execute(
                "SELECT COUNT(*) n FROM author_flags WHERE event_id=? AND level='L1'", (ev["id"],)).fetchone()["n"]
            out.append(d)
        return out

    @staticmethod
    def search(conn, q):
        from ingest.common import name_sort
        q = q.strip()
        if not q:
            return {"authors": [], "papers": []}
        like = f"%{q}%"
        like_norm = f"%{name_sort(q)}%"
        authors = rows_to_list(conn.execute(
            """SELECT DISTINCT a.id, a.name_display, a.name_zh, a.tier,
                      (SELECT COUNT(*) FROM paper_authors pa WHERE pa.author_id=a.id) AS papers,
                      (SELECT level FROM author_flags af WHERE af.author_id=a.id AND af.level='L0'
                        AND af.status!='dismissed' LIMIT 1) AS l0,
                      (SELECT level FROM author_flags af WHERE af.author_id=a.id AND af.level='L1'
                        AND af.status!='dismissed' LIMIT 1) AS l1
               FROM authors a LEFT JOIN author_aliases al ON al.author_id=a.id
               WHERE a.name_display LIKE ? OR a.name_sort LIKE ? OR a.name_zh LIKE ? OR al.alias LIKE ?
               ORDER BY papers DESC LIMIT 40""", (like, like_norm, like, like)))
        papers = rows_to_list(conn.execute(
            """SELECT id, title, year, journal, pmid, retraction_status FROM papers
               WHERE title LIKE ? LIMIT 40""", (like,)))
        return {"authors": authors, "papers": papers}

    @staticmethod
    def queue(conn):
        return rows_to_list(conn.execute(
            """SELECT al.id, al.author_id, al.alias, al.alias_type, al.source, al.confidence,
                      a.name_display FROM author_aliases al
               JOIN authors a ON a.id=al.author_id
               WHERE al.verified=0 ORDER BY al.confidence DESC, al.id LIMIT 200"""))

    # ---------- 写操作（全部留痕） ----------

    @staticmethod
    def verify_alias(conn, alias_id, body):
        ok = 1 if body.get("verified", True) else -1
        by = body.get("by", "user")
        conn.execute("UPDATE author_aliases SET verified=?, verified_by=?, verified_at=datetime('now') WHERE id=?",
                     (ok, by, alias_id))
        row = conn.execute("SELECT * FROM author_aliases WHERE id=?", (alias_id,)).fetchone()
        audit(conn, f"user:{by}", "alias.verify" if ok == 1 else "alias.reject",
              "author", row["author_id"], {"alias": row["alias"], "type": row["alias_type"]})
        conn.commit()
        return {"ok": True}

    @staticmethod
    def add_hanzi(conn, aid, body):
        hanzi = body.get("hanzi", "").strip()
        by = body.get("by", "user")
        if not re.search(r"[\u4e00-\u9fff]", hanzi):
            return {"error": "需包含汉字"}
        conn.execute(
            """INSERT OR REPLACE INTO author_aliases(author_id, alias, alias_type, source, confidence,
               verified, verified_by, verified_at) VALUES(?, ?, 'hanzi', 'manual', 1.0, 1, ?, datetime('now'))""",
            (aid, hanzi, by))
        conn.execute("UPDATE authors SET name_zh=?, updated_at=datetime('now') WHERE id=?", (hanzi, aid))
        audit(conn, f"user:{by}", "author.set_hanzi", "author", aid, {"hanzi": hanzi})
        conn.commit()
        return {"ok": True}

    @staticmethod
    def confirm_l0(conn, flag_id, body):
        by = body.get("by", "user")
        basis = body.get("basis", "")
        conn.execute(
            "UPDATE author_flags SET status='confirmed', confirmed_by=?, basis=basis||? WHERE id=? AND level='L0'",
            (by, f" ｜人工定性：{basis}" if basis else "", flag_id))
        row = conn.execute("SELECT * FROM author_flags WHERE id=?", (flag_id,)).fetchone()
        audit(conn, f"user:{by}", "flag.l0_confirm", "author", row["author_id"],
              {"flag_id": flag_id, "basis": basis})
        conn.commit()
        return {"ok": True}

    @staticmethod
    def confirm_event(conn, eid, body):
        by = body.get("by", "user")
        conn.execute(
            "UPDATE fraud_events SET status='confirmed', confirmed_at=datetime('now'), confirmed_by=? WHERE id=?",
            (by, eid))
        ev = conn.execute("SELECT slug FROM fraud_events WHERE id=?", (eid,)).fetchone()
        audit(conn, f"user:{by}", "event.confirm", "fraud_event", eid, {"slug": ev["slug"]})
        # 生成修订报告
        from manage.events import build_report
        report = build_report(conn, conn.execute("SELECT * FROM fraud_events WHERE id=?", (eid,)).fetchone())
        out = ROOT / "data" / f"revision_{ev['slug']}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        conn.commit()
        return {"ok": True, "report": str(out)}

    @staticmethod
    def dismiss_flag(conn, flag_id, body):
        by = body.get("by", "user")
        conn.execute("UPDATE author_flags SET status='dismissed', confirmed_by=? WHERE id=?", (by, flag_id))
        row = conn.execute("SELECT * FROM author_flags WHERE id=?", (flag_id,)).fetchone()
        audit(conn, f"user:{by}", "flag.dismiss", "author", row["author_id"], {"flag_id": flag_id})
        conn.commit()
        return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "peopar/0.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj):
        self._send(200, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        conn = connect()
        init_db(conn)
        try:
            if path == "/":
                self._send(200, (WEB / "index.html").read_text(encoding="utf-8"),
                           "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                f = STATIC / path[len("/static/"):].replace("..", "")
                if f.exists():
                    ctype = "application/javascript" if f.suffix == ".js" else "application/octet-stream"
                    self._send(200, f.read_bytes(), ctype)
                else:
                    self._send(404, "not found", "text/plain")
            elif path == "/api/domains":
                self._json(API.domains(conn))
            elif path == "/api/graph":
                self._json(API.graph(conn, qs.get("domain", [""])[0]))
            elif path == "/api/search":
                self._json(API.search(conn, qs.get("q", [""])[0]))
            elif path == "/api/queue":
                self._json(API.queue(conn))
            elif path == "/api/events":
                self._json(API.events(conn))
            elif m := re.fullmatch(r"/api/author/(BG\d+)", path):
                self._json(API.author(conn, m.group(1)))
            elif m := re.fullmatch(r"/api/event/(\d+)", path):
                self._json(API.event(conn, int(m.group(1))))
            elif m := re.fullmatch(r"/api/audit", path):
                self._json(rows_to_list(conn.execute(
                    "SELECT ts, actor, action, entity_type, entity_id, detail FROM audit_log "
                    "ORDER BY id DESC LIMIT 120")))
            else:
                self._send(404, "not found", "text/plain")
        finally:
            conn.close()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        body = jbody(self)
        conn = connect()
        init_db(conn)
        try:
            if m := re.fullmatch(r"/api/alias/(\d+)/verify", path):
                self._json(API.verify_alias(conn, int(m.group(1)), body))
            elif m := re.fullmatch(r"/api/author/(BG\d+)/hanzi", path):
                self._json(API.add_hanzi(conn, m.group(1), body))
            elif m := re.fullmatch(r"/api/flag/(\d+)/confirm-l0", path):
                self._json(API.confirm_l0(conn, int(m.group(1)), body))
            elif m := re.fullmatch(r"/api/flag/(\d+)/dismiss", path):
                self._json(API.dismiss_flag(conn, int(m.group(1)), body))
            elif m := re.fullmatch(r"/api/event/(\d+)/confirm", path):
                self._json(API.confirm_event(conn, int(m.group(1)), body))
            else:
                self._send(404, "not found", "text/plain")
        finally:
            conn.close()


def main():
    conn = connect()
    init_db(conn)
    conn.close()
    print(f"百官行述 已启动 → http://127.0.0.1:{PORT}")
    print("Ctrl+C 退出")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
