"""Researcher Atlas · 本地应用（零依赖：标准库 http.server + sqlite3）。

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
        g = json.loads(path.read_text(encoding="utf-8"))
        # 方向名由最新快照驱动：合并簇名与快照审阅状态，供可视化按研究方向命名
        meta = {r["id"]: (r["name"], r["review_status"], r["display"]) for r in conn.execute(
            """SELECT c.id, c.name, c.display,
                      (SELECT s.review_status FROM snapshots s
                       WHERE s.cluster_id=c.id AND s.status!='superseded'
                       ORDER BY s.id DESC LIMIT 1) AS review_status
               FROM clusters c WHERE c.domain_id=?""", (domain,))}
        for c in g.get("clusters", []):
            name, review, display = meta.get(c.get("cluster_id"), (None, None, "normal"))
            c["name"] = name
            c["snap_review"] = review
            c["display"] = display
        return g

    @staticmethod
    def health():
        return {"ok": True, "app": "peopar", "version": "0.2"}

    @staticmethod
    def latest_clusters(conn, domain, limit=None, min_size=3):
        """该域最新批次的簇（方向簇只增不改，展示用最新批次）。
        只取规模 ≥min_size 的 top limit 个主要方向，避免高度分散的小簇淹没视图。"""
        q = """SELECT c.* FROM clusters c
               JOIN (SELECT cluster_id FROM author_clusters
                     GROUP BY cluster_id HAVING COUNT(*)>=?) s ON s.cluster_id=c.id
               WHERE c.domain_id=? AND c.batch_id=
                 (SELECT MAX(batch_id) FROM clusters WHERE domain_id=?)
               ORDER BY (SELECT COUNT(*) FROM author_clusters ac WHERE ac.cluster_id=c.id) DESC"""
        if limit:
            q += f" LIMIT {int(limit)}"
        return conn.execute(q, (min_size, domain, domain)).fetchall()

    @staticmethod
    def directions(conn, domain):
        """方向聚合图：方向（簇）为节点，方向间共享论文为边；节点大小=规模/热度。
        只展示主要方向（规模 top 40，≥3 人），小簇不进入聚合视图。"""
        clusters = API.latest_clusters(conn, domain, limit=40)
        if not clusters:
            return {"error": f"该域暂无方向簇：请先运行 python3 analyze/graph.py {domain}"}
        cids = [c["id"] for c in clusters]
        ph = ",".join("?" * len(cids))
        cur_year = 2026
        recent_from = cur_year - 2
        stats = {}
        for r in conn.execute(
                f"""SELECT ac.cluster_id,
                           COUNT(DISTINCT pa.paper_id) AS papers,
                           COUNT(DISTINCT CASE WHEN p.year>=? THEN pa.paper_id END) AS recent
                    FROM author_clusters ac
                    JOIN paper_authors pa ON pa.author_id=ac.author_id
                    JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
                    JOIN papers p ON p.id=pa.paper_id
                    WHERE ac.cluster_id IN ({ph})
                    GROUP BY ac.cluster_id""",
                [recent_from, domain] + cids):
            stats[r["cluster_id"]] = dict(r)
        cit = {}
        for r in conn.execute(
                f"""SELECT cluster_id, SUM(c) AS citations FROM (
                       SELECT DISTINCT ac.cluster_id, p.cited_by_count AS c
                       FROM author_clusters ac
                       JOIN paper_authors pa ON pa.author_id=ac.author_id
                       JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
                       JOIN papers p ON p.id=pa.paper_id
                       WHERE ac.cluster_id IN ({ph}))
                    GROUP BY cluster_id""",
                [domain] + cids):
            cit[r["cluster_id"]] = r["citations"] or 0
        size_map = {}
        for r in conn.execute(
                f"SELECT cluster_id, COUNT(*) n FROM author_clusters WHERE cluster_id IN ({ph}) GROUP BY cluster_id",
                cids):
            size_map[r["cluster_id"]] = r["n"]
        top = {}
        for r in conn.execute(
                f"""SELECT ac.cluster_id, a.id, a.name_display, a.name_zh, a.tier,
                           COUNT(DISTINCT pa.paper_id) AS np
                    FROM author_clusters ac
                    JOIN authors a ON a.id=ac.author_id
                    LEFT JOIN paper_authors pa ON pa.author_id=a.id
                    LEFT JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
                    WHERE ac.cluster_id IN ({ph})
                    GROUP BY ac.cluster_id, a.id
                    ORDER BY ac.cluster_id, np DESC""",
                [domain] + cids):
            top.setdefault(r["cluster_id"], []).append(
                {"id": r["id"], "name": r["name_display"], "zh": r["name_zh"],
                 "tier": r["tier"], "papers": r["np"]})
        snap = {}
        for r in conn.execute(
                f"""SELECT s.cluster_id, s.review_status FROM snapshots s
                    WHERE s.cluster_id IN ({ph}) AND s.status!='superseded'
                    ORDER BY s.id DESC""", cids):
            snap.setdefault(r["cluster_id"], r["review_status"])
        directions = []
        for c in clusters:
            st = stats.get(c["id"], {})
            directions.append({
                "cluster_id": c["id"], "label": c["label"], "name": c["name"],
                "display": c["display"],
                "size": size_map.get(c["id"], 0),
                "papers": st.get("papers", 0), "recent": st.get("recent", 0),
                "citations": cit.get(c["id"], 0),
                "snap_review": snap.get(c["id"]),
                "top_authors": (top.get(c["id"]) or [])[:5],
            })
        links = []
        seen = set()
        for r in conn.execute(
                f"""SELECT ac1.cluster_id AS c1, ac2.cluster_id AS c2, COUNT(DISTINCT pa.paper_id) AS n
                    FROM author_clusters ac1
                    JOIN paper_authors pa ON pa.author_id=ac1.author_id
                    JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
                    JOIN author_clusters ac2 ON ac2.author_id=pa.author_id AND ac2.cluster_id!=ac1.cluster_id
                    WHERE ac1.cluster_id IN ({ph}) AND ac2.cluster_id IN ({ph})
                    GROUP BY ac1.cluster_id, ac2.cluster_id HAVING n>=2""",
                [domain] + cids + cids):
            k = (r["c1"], r["c2"]) if r["c1"] < r["c2"] else (r["c2"], r["c1"])
            if k in seen:
                continue
            seen.add(k)
            links.append({"source": r["c1"], "target": r["c2"], "shared_papers": r["n"]})
        directions.sort(key=lambda d: -d["size"])
        return {"domain": domain, "directions": directions, "links": links}

    @staticmethod
    def direction_researchers(conn, cid):
        """方向 → 研究者清单：该方向的核心研究者（画像/机构/代表作/联系线索）。"""
        c = conn.execute("SELECT * FROM clusters WHERE id=?", (cid,)).fetchone()
        if not c:
            return {"error": "簇不存在"}
        members = conn.execute(
            """SELECT a.id, a.name_display, a.name_zh, a.tier, a.orcid, a.openalex_id,
                      COUNT(DISTINCT pa.paper_id) AS papers,
                      (SELECT COUNT(DISTINCT pa2.paper_id) FROM paper_authors pa2
                       JOIN paper_domains pd2 ON pd2.paper_id=pa2.paper_id AND pd2.domain_id=?
                       WHERE pa2.author_id=a.id) AS domain_papers
               FROM author_clusters ac
               JOIN authors a ON a.id=ac.author_id
               LEFT JOIN paper_authors pa ON pa.author_id=a.id
               WHERE ac.cluster_id=?
               GROUP BY a.id ORDER BY domain_papers DESC, a.name_display LIMIT 60""",
            (c["domain_id"], cid)).fetchall()
        researchers = []
        for m in members:
            aff = conn.execute(
                """SELECT institution, start_year, end_year, source_tag, verified FROM affiliations
                   WHERE author_id=? ORDER BY (verified=1) DESC, source_tag='web' DESC, start_year
                   LIMIT 1""", (m["id"],)).fetchone()
            snap = conn.execute(
                """SELECT id, content, review_status, generated_at FROM author_snapshots
                   WHERE author_id=? AND status!='superseded' ORDER BY id DESC LIMIT 1""",
                (m["id"],)).fetchone()
            snap_out = None
            if snap:
                try:
                    snap_out = {**json.loads(snap["content"]), "review_status": snap["review_status"]}
                except json.JSONDecodeError:
                    snap_out = {"review_status": snap["review_status"]}
            reps = conn.execute(
                """SELECT p.id, p.title, p.year, p.pmid, p.cited_by_count FROM paper_authors pa
                   JOIN papers p ON p.id=pa.paper_id
                   JOIN paper_domains pd ON pd.paper_id=p.id AND pd.domain_id=?
                   WHERE pa.author_id=? ORDER BY p.cited_by_count DESC LIMIT 3""",
                (c["domain_id"], m["id"])).fetchall()
            researchers.append({
                "id": m["id"], "name": m["name_display"], "zh": m["name_zh"],
                "tier": m["tier"], "papers": m["domain_papers"],
                "institution": dict(aff) if aff else None,
                "snapshot": snap_out,
                "representative": [dict(r) for r in reps],
                "contact": {"orcid": m["orcid"], "openalex_id": m["openalex_id"]},
            })
        return {"cluster_id": cid, "name": c["name"], "label": c["label"],
                "display": c["display"], "domain": c["domain_id"], "researchers": researchers}

    @staticmethod
    def layout(conn, domain):
        """信息化图谱布局：优先返回 analyze/layout.py 的完整 JSON（三型边+摘要/笔记）。"""
        f = ROOT / "data" / f"layout_{domain}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return {"error": f"无布局：请运行 python3 analyze/layout.py {domain} --out data/layout_{domain}.json"}

    @staticmethod
    def trends(conn, domain):
        """热点时间演化：方向×年份论文分布 + 近三年活跃榜（主要方向 top 40）。"""
        clusters = API.latest_clusters(conn, domain, limit=40)
        cids = [c["id"] for c in clusters]
        if not cids:
            return {"error": "该域暂无方向簇"}
        ph = ",".join("?" * len(cids))
        cur_year = 2026
        series = {}
        for r in conn.execute(
                f"""SELECT ac.cluster_id, p.year, COUNT(DISTINCT pa.paper_id) AS n
                    FROM author_clusters ac
                    JOIN paper_authors pa ON pa.author_id=ac.author_id
                    JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
                    JOIN papers p ON p.id=pa.paper_id
                    WHERE ac.cluster_id IN ({ph}) AND p.year IS NOT NULL
                    GROUP BY ac.cluster_id, p.year""",
                [domain] + cids):
            series.setdefault(r["cluster_id"], {})[r["year"]] = r["n"]
        out = []
        for c in clusters:
            years = series.get(c["id"], {})
            if not years:
                continue
            recent = sum(v for y, v in years.items() if y >= cur_year - 2)
            prev = sum(v for y, v in years.items() if cur_year - 5 <= y < cur_year - 2)
            out.append({
                "cluster_id": c["id"], "label": c["label"], "name": c["name"],
                "display": c["display"], "years": years,
                "recent": recent, "prev": prev,
                "growth": (recent - prev) / prev if prev else (1.0 if recent else 0.0),
            })
        out.sort(key=lambda d: -d["recent"])
        return {"domain": domain, "series": out[:20]}

    @staticmethod
    def paper(conn, pid):
        """论文详情（DB 权威；摘要显示人工覆盖优先）。"""
        r = conn.execute(
            "SELECT id, title, title_cn, year, journal, doi, pmid, openalex_id, abstract, abstract_override, "
            "note, cited_by_count, retraction_status FROM papers WHERE id=?", (pid,)).fetchone()
        if not r:
            return {"error": "论文不存在"}
        out = dict(r)
        out["display_abstract"] = r["abstract_override"] or r["abstract"]
        out["authors"] = rows_to_list(conn.execute(
            """SELECT a.id, a.name_display, pa.position FROM paper_authors pa
               JOIN authors a ON a.id=pa.author_id WHERE pa.paper_id=? ORDER BY pa.position""", (pid,)))
        return out

    @staticmethod
    def paper_edit(conn, pid, body):
        by = body.get("by", "user")
        sets, args = [], []
        if "note" in body:
            sets.append("note=?"); args.append(body.get("note"))
        if "abstract_override" in body:
            sets.append("abstract_override=?"); args.append(body.get("abstract_override"))
        if "title_cn" in body:
            sets.append("title_cn=?"); args.append(body.get("title_cn"))
        if not sets:
            return {"error": "无可更新字段（note / abstract_override）"}
        args.append(pid)
        conn.execute(f"UPDATE papers SET {', '.join(sets)}, updated_at=datetime('now') WHERE id=?", args)
        audit(conn, f"user:{by}", "paper.edit", "paper", pid, body)
        conn.commit()
        return {"ok": True}

    @staticmethod
    def direction_manual(conn, cid, body):
        """方向人工修订：替换最新 active 快照 content（修订即终版，by=user）。"""
        by = body.get("by", "user")
        content = body.get("content")
        if not content or not isinstance(content, dict):
            return {"error": "content 必填对象"}
        s = conn.execute(
            """SELECT s.id, s.review_status, s.status FROM snapshots s
               WHERE s.cluster_id=? AND s.status!='superseded' ORDER BY s.id DESC LIMIT 1""",
            (cid,)).fetchone()
        if not s:
            return {"error": "该方向无快照"}
        conn.execute(
            "UPDATE snapshots SET content=?, model=COALESCE(model,'')||' |manual', "
            "review_status='approved', reviewed_by=?, reviewed_at=datetime('now') WHERE id=?",
            (json.dumps(content, ensure_ascii=False), by, s["id"]))
        if content.get("name"):
            conn.execute("UPDATE clusters SET name=? WHERE id=?", (content["name"], cid))
        audit(conn, f"user:{by}", "snapshot.manual", "cluster", cid, {"snapshot_id": s["id"]})
        conn.commit()
        return {"ok": True}

    @staticmethod
    def author_tags(conn, aid):
        return rows_to_list(conn.execute(
            """SELECT v.tag, v.dim, t.status, t.basis FROM researcher_tags t
               JOIN tag_vocab v ON v.id=t.tag_id
               WHERE t.author_id=? AND t.status!='rejected' ORDER BY v.dim""", (aid,)))

    @staticmethod
    def author_snapshot(conn, aid):
        s = conn.execute(
            """SELECT s.*, a.name_display, a.name_zh FROM author_snapshots s
               JOIN authors a ON a.id=s.author_id
               WHERE s.author_id=? AND s.status!='superseded' ORDER BY s.id DESC LIMIT 1""",
            (aid,)).fetchone()
        if not s:
            return None
        info = dict(s)
        try:
            content = json.loads(s["content"])
            info["content"] = content
        except json.JSONDecodeError:
            content = {}
            info["content"] = {"raw": s["content"]}
        # 代表论文从 content.representative_paper_ids 解析（作者画像不写 evidence 表）
        reps = content.get("representative_paper_ids", [])
        info["evidence"] = []
        if reps:
            ph = ",".join("?" * len(reps))
            info["evidence"] = rows_to_list(conn.execute(
                f"""SELECT 'representative' AS role, p.id AS paper_id, p.title, p.year, p.journal,
                           p.pmid, p.doi, p.cited_by_count, p.retraction_status
                    FROM papers p WHERE p.id IN ({ph})
                    ORDER BY CASE p.id {"".join(f" WHEN ? THEN {i}" for i in range(len(reps)))} END""",
                reps + reps))
        return info

    @staticmethod
    def judgments(conn, status=None):
        q = "SELECT * FROM judgments"
        rows = conn.execute(q + (" WHERE status=?" if status else "") + " ORDER BY id",
                            (status,) if status else ()).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["proposal"] = json.loads(r["proposal"])
            except json.JSONDecodeError:
                pass
            out.append(d)
        return out

    @staticmethod
    def webvpn_imports(conn, domain=None):
        q = "SELECT * FROM webvpn_imports"
        rows = conn.execute(q + (" WHERE domain_id=?" if domain else "") + " ORDER BY id DESC",
                            (domain,) if domain else ()).fetchall()
        return rows_to_list(rows)

    @staticmethod
    def affiliation_queue(conn):
        return rows_to_list(conn.execute(
            """SELECT af.id, af.author_id, a.name_display, a.name_zh, af.institution, af.source_tag,
                      af.source_url, af.note, af.confidence
               FROM affiliations af JOIN authors a ON a.id=af.author_id
               WHERE af.source_tag='web' AND af.verified=0 ORDER BY af.id LIMIT 200"""))

    @staticmethod
    def verify_affiliation(conn, aff_id, body):
        by = body.get("by", "user")
        row = conn.execute("SELECT author_id FROM affiliations WHERE id=?", (aff_id,)).fetchone()
        if not row:
            return {"error": "履历不存在"}
        conn.execute("UPDATE affiliations SET verified=1 WHERE id=?", (aff_id,))
        audit(conn, f"user:{by}", "affiliation.verify", "author", row["author_id"], {"aff_id": aff_id})
        conn.commit()
        return {"ok": True}

    @staticmethod
    def webvpn_import(conn, body):
        from ingest.webvpn_import import import_records, parse_ris, parse_scopus_csv
        source = body.get("source")
        domain = body.get("domain")
        content = body.get("content", "")
        if source not in ("scopus", "cnki", "wanfang") or not content:
            return {"error": "需要 source（scopus|cnki|wanfang）与 content"}
        fmt = body.get("format") or ("csv" if source == "scopus" else "ris")
        records = parse_scopus_csv(content) if fmt == "csv" else parse_ris(content)
        if not records:
            return {"error": "未解析到题录，请检查导出格式"}
        import hashlib
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]
        dup = conn.execute("SELECT id FROM webvpn_imports WHERE file_hash=?", (h,)).fetchone()
        if dup:
            return {"ok": True, "duplicate": True, "batch_id": dup["id"], "records": len(records)}
        n_new, n_dup, n_authors = import_records(conn, domain, records, source, body.get("query", ""))
        cur = conn.execute(
            """INSERT INTO webvpn_imports(domain_id, source, file_name, file_hash, query,
               n_records, n_new, n_dup) VALUES(?,?,?,?,?,?,?,?)""",
            (domain, source, body.get("file_name"), h, body.get("query"), len(records), n_new, n_dup))
        from ingest.common import audit
        audit(conn, f"user:{body.get('by', 'user')}", "webvpn.import", "domain", domain,
              {"source": source, "records": len(records), "new": n_new, "dup": n_dup})
        conn.commit()
        return {"ok": True, "batch_id": cur.lastrowid, "records": len(records),
                "n_new": n_new, "n_dup": n_dup, "n_authors": n_authors}

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

    @staticmethod
    def snapshots(conn, domain=None):
        q = """SELECT s.id, s.cluster_id, c.domain_id, s.content, s.model, s.prompt_ver,
                      s.review_status, s.reviewed_by, s.generated_at,
                      (SELECT COUNT(*) FROM evidence e WHERE e.snapshot_id=s.id) AS n_evidence
               FROM snapshots s JOIN clusters c ON c.id=s.cluster_id
               WHERE s.status!='superseded'"""
        rows = conn.execute(q + (" AND c.domain_id=? ORDER BY s.id DESC" if domain
                                 else " ORDER BY s.id DESC"),
                            (domain,) if domain else ()).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["content"] = json.loads(r["content"])
            except json.JSONDecodeError:
                d["content"] = {"raw": r["content"]}
            out.append(d)
        return out

    @staticmethod
    def cluster_snapshot(conn, cluster_id):
        s = conn.execute(
            """SELECT s.*, c.domain_id FROM snapshots s JOIN clusters c ON c.id=s.cluster_id
               WHERE s.cluster_id=? AND s.status!='superseded' ORDER BY s.id DESC LIMIT 1""",
            (cluster_id,)).fetchone()
        if not s:
            return None
        info = dict(s)
        try:
            info["content"] = json.loads(s["content"])
        except json.JSONDecodeError:
            info["content"] = {"raw": s["content"]}
        info["evidence"] = rows_to_list(conn.execute(
            """SELECT e.role, p.id AS paper_id, p.title, p.year, p.journal, p.pmid, p.doi,
                      p.cited_by_count, p.retraction_status
               FROM evidence e JOIN papers p ON p.id=e.paper_id
               WHERE e.snapshot_id=? ORDER BY e.role, p.cited_by_count DESC""", (s["id"],)))
        return info

    @staticmethod
    def snapshot_queue(conn):
        return rows_to_list(conn.execute(
            """SELECT s.id, s.cluster_id, c.domain_id, s.content, s.model, s.generated_at,
                      (SELECT COUNT(*) FROM evidence e WHERE e.snapshot_id=s.id) AS n_evidence
               FROM snapshots s JOIN clusters c ON c.id=s.cluster_id
               WHERE s.review_status='pending' AND s.status!='superseded'
               ORDER BY s.id LIMIT 100"""))

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
    def set_note(conn, aid, body):
        """研究者笔记（自由批注/联系备注）→ authors.note，by=user。"""
        by = body.get("by", "user")
        note = (body.get("note") or "").strip()
        conn.execute("UPDATE authors SET note=? WHERE id=?", (note or None, aid))
        audit(conn, f"user:{by}", "author.note", "author", aid, {"note": note[:200]})
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

    @staticmethod
    def review_snapshot(conn, sid, body):
        action = body.get("action")
        if action not in ("approve", "reject"):
            return {"error": "action 必须是 approve 或 reject"}
        by = body.get("by", "user")
        row = conn.execute("SELECT id, review_status FROM snapshots WHERE id=?", (sid,)).fetchone()
        if not row:
            return {"error": "快照不存在"}
        status = "approved" if action == "approve" else "rejected"
        conn.execute("UPDATE snapshots SET review_status=?, reviewed_by=?, reviewed_at=datetime('now') WHERE id=?",
                     (status, by, sid))
        audit(conn, f"user:{by}", f"snapshot.{action}", "snapshot", sid, None)
        conn.commit()
        return {"ok": True}

    @staticmethod
    def review_author_snapshot(conn, sid, body):
        action = body.get("action")
        if action not in ("approve", "reject"):
            return {"error": "action 必须是 approve 或 reject"}
        by = body.get("by", "user")
        row = conn.execute("SELECT id, review_status FROM author_snapshots WHERE id=?", (sid,)).fetchone()
        if not row:
            return {"error": "作者快照不存在"}
        status = "approved" if action == "approve" else "rejected"
        conn.execute("UPDATE author_snapshots SET review_status=?, reviewed_by=?, reviewed_at=datetime('now') WHERE id=?",
                     (status, by, sid))
        audit(conn, f"user:{by}", f"author_snapshot.{action}", "author",
              row["author_id"], {"snapshot_id": sid})
        conn.commit()
        return {"ok": True}

    @staticmethod
    def judgment_decide(conn, jid, body):
        action = body.get("action")
        if action not in ("accept", "reject"):
            return {"error": "action 必须是 accept 或 reject"}
        status = "accepted" if action == "accept" else "rejected"
        by = body.get("by", "user")
        row = conn.execute("SELECT * FROM judgments WHERE id=?", (jid,)).fetchone()
        if not row:
            return {"error": "提案不存在"}
        if row["status"] != "pending":
            return {"error": f"提案已裁决（{row['status']}）"}
        if action == "accept":
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
        conn.execute(
            "UPDATE judgments SET status=?, decided_by=?, decided_at=datetime('now'), decision_note=? WHERE id=?",
            (status, by, body.get("note"), jid))
        conn.execute(
            """UPDATE judgments SET status='superseded' WHERE jtype=? AND entity_type=? AND entity_id=?
               AND status='accepted' AND id!=?""",
            (row["jtype"], row["entity_type"], row["entity_id"], jid))
        audit(conn, f"user:{by}", f"judgment.{action}", row["entity_type"], row["entity_id"],
              {"judgment_id": jid, "jtype": row["jtype"], "note": body.get("note")})
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

    def _safe(self, fn):
        """执行 API 调用；异常转为 500 JSON，避免线程崩溃。"""
        try:
            return fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
            except Exception:
                pass
            return None

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        conn = connect()
        init_db(conn)
        try:
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
                elif path == "/api/health":
                    self._json(API.health())
                elif path == "/api/directions":
                    self._json(API.directions(conn, qs.get("domain", [""])[0]))
                elif path == "/api/layout":
                    self._json(API.layout(conn, qs.get("domain", [""])[0]))
                elif path == "/api/trends":
                    self._json(API.trends(conn, qs.get("domain", [""])[0]))
                elif path == "/api/judgments":
                    self._json(API.judgments(conn, qs.get("status", [None])[0]))
                elif path == "/api/webvpn-imports":
                    self._json(API.webvpn_imports(conn, qs.get("domain", [None])[0]))
                elif path == "/api/affiliation-queue":
                    self._json(API.affiliation_queue(conn))
                elif path == "/api/graph":
                    self._json(API.graph(conn, qs.get("domain", [""])[0]))
                elif path == "/api/search":
                    self._json(API.search(conn, qs.get("q", [""])[0]))
                elif path == "/api/queue":
                    self._json(API.queue(conn))
                elif path == "/api/snapshots":
                    self._json(API.snapshots(conn, qs.get("domain", [None])[0]))
                elif path == "/api/snapshot-queue":
                    self._json(API.snapshot_queue(conn))
                elif path == "/api/events":
                    self._json(API.events(conn))
                elif m := re.fullmatch(r"/api/direction/(\d+)/researchers", path):
                    self._json(API.direction_researchers(conn, int(m.group(1))))
                elif m := re.fullmatch(r"/api/cluster/(\d+)/snapshot", path):
                    self._json(API.cluster_snapshot(conn, int(m.group(1))) or {})
                elif m := re.fullmatch(r"/api/author/(BG\d+)", path):
                    self._json(API.author(conn, m.group(1)))
                elif m := re.fullmatch(r"/api/paper/(\d+)", path):
                    self._json(API.paper(conn, int(m.group(1))))
                elif m := re.fullmatch(r"/api/author/(BG\d+)/snapshot", path):
                    self._json(API.author_snapshot(conn, m.group(1)) or {})
                elif m := re.fullmatch(r"/api/author/(BG\d+)/tags", path):
                    self._json(API.author_tags(conn, m.group(1)))
                elif m := re.fullmatch(r"/api/event/(\d+)", path):
                    self._json(API.event(conn, int(m.group(1))))
                elif m := re.fullmatch(r"/api/audit", path):
                    self._json(rows_to_list(conn.execute(
                        "SELECT ts, actor, action, entity_type, entity_id, detail FROM audit_log "
                        "ORDER BY id DESC LIMIT 120")))
                else:
                    self._send(404, "not found", "text/plain")
            except Exception as e:
                import traceback
                traceback.print_exc()
                try:
                    self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
                except Exception:
                    pass
        finally:
            conn.close()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        body = jbody(self)
        conn = connect()
        init_db(conn)
        try:
            try:
                if m := re.fullmatch(r"/api/alias/(\d+)/verify", path):
                    self._json(API.verify_alias(conn, int(m.group(1)), body))
                elif m := re.fullmatch(r"/api/author/(BG\d+)/note", path):
                    self._json(API.set_note(conn, m.group(1), body))
                elif m := re.fullmatch(r"/api/author/(BG\d+)/hanzi", path):
                    self._json(API.add_hanzi(conn, m.group(1), body))
                elif m := re.fullmatch(r"/api/flag/(\d+)/confirm-l0", path):
                    self._json(API.confirm_l0(conn, int(m.group(1)), body))
                elif m := re.fullmatch(r"/api/flag/(\d+)/dismiss", path):
                    self._json(API.dismiss_flag(conn, int(m.group(1)), body))
                elif m := re.fullmatch(r"/api/event/(\d+)/confirm", path):
                    self._json(API.confirm_event(conn, int(m.group(1)), body))
                elif m := re.fullmatch(r"/api/snapshot/(\d+)/review", path):
                    self._json(API.review_snapshot(conn, int(m.group(1)), body))
                elif m := re.fullmatch(r"/api/author-snapshot/(\d+)/review", path):
                    self._json(API.review_author_snapshot(conn, int(m.group(1)), body))
                elif m := re.fullmatch(r"/api/judgment/(\d+)/decide", path):
                    self._json(API.judgment_decide(conn, int(m.group(1)), body))
                elif path == "/api/webvpn/import":
                    self._json(API.webvpn_import(conn, body))
                elif m := re.fullmatch(r"/api/paper/(\d+)/edit", path):
                    self._json(API.paper_edit(conn, int(m.group(1)), body))
                elif m := re.fullmatch(r"/api/direction/(\d+)/manual", path):
                    self._json(API.direction_manual(conn, int(m.group(1)), body))
                elif m := re.fullmatch(r"/api/affiliation/(\d+)/verify", path):
                    self._json(API.verify_affiliation(conn, int(m.group(1)), body))
                else:
                    self._send(404, "not found", "text/plain")
            except Exception as e:
                import traceback
                traceback.print_exc()
                try:
                    self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
                except Exception:
                    pass
        finally:
            conn.close()


def main():
    conn = connect()
    init_db(conn)
    conn.close()
    print(f"Researcher Atlas 已启动 → http://127.0.0.1:{PORT}")
    print("Ctrl+C 退出")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
