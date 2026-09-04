"""导出 vault 投影：把核心数据（方向/核心研究者/高被引论文/事件）导出为 Obsidian 仓库内的 markdown。

三层架构：
  L1 权威层  SQLite + CLI（采集/图谱/合成/写操作/审计）
  L2 投影层  本文件：vault 内 <方向名>/peopar/ 目录的 md 笔记（frontmatter + wikilink），幂等可重跑
  L3 展示层  Obsidian 插件：静态优先读这些 md 渲染（零服务器）；更新由 skill/agent 触发本文件

文件结构（按研究大方向分层）：
  <vault>/
  └── <方向名>/                  # --topic 指定（如 神经语言学与失语症）
      └── peopar/
          ├── _sync.md               同步时间 + 数据基线（frontmatter）
          ├── <domain>.peopar        域视图入口（插件扩展名触发）
          ├── directions/direction-<cid>.md   方向笔记（方案 C：callout + 折叠叙述 + 链接清单）
          ├── researchers/BG<id>.md  研究者笔记（研究方向三层：所属方向/代表作/画像 focus）
          ├── papers/paper-<id>.md   论文笔记（P2：元数据 + 方向角色锚定句 + 作者 wikilink）
          └── events/event-<id>.md   事件笔记

用法：
  python3 manage/export_vault.py <vault路径> --topic "方向名" [--domain neuroling,ad_lesne]
        [--researcher-min-papers 3] [--top-papers 300] [--all-researchers]
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import connect, init_db

def slug_direction(cid): return f"direction-{cid}"
def slug_researcher(aid): return aid
def slug_paper(pid): return f"paper-{pid}"
def slug_event(eid): return f"event-{eid}"


def fm(d: dict) -> str:
    """简单 YAML frontmatter 序列化（字符串加引号；列表内字符串元素加引号防特殊字符破坏 YAML）。"""
    lines = ["---"]
    for k, v in d.items():
        if isinstance(v, list):
            parts = [f'"{str(x)}"' if isinstance(x, str) else str(x) for x in v]
            lines.append(f"{k}: [{', '.join(parts)}]")
        elif isinstance(v, dict):
            lines.append(f"{k}: '{json.dumps(v, ensure_ascii=False)}'")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def md_file(out: Path, name: str, front: dict, body: str):
    (out / name).write_text(fm(front) + "\n\n" + body, encoding="utf-8")


# ---------- 方向角色提取：从快照/画像文本中找出锚定某 paper_id 的句子 ----------
_PAPER_REF = re.compile(r"\(?paper\s*(\d+)\)?")
def _sentences(text: str):
    return [s.strip() for s in re.split(r"(?<=[。；;\n])", text or "") if s.strip()]


def find_paper_sentences(text: str, pid: int) -> list:
    """返回文本中明确提到 paper <pid> 的句子（≤2 句）。句子天然锚定该论文，可安全写入论文笔记。"""
    hits = []
    for s in _sentences(text):
        if _PAPER_REF.search(s) and str(pid) in {m.group(1) for m in _PAPER_REF.finditer(s)}:
            hits.append(s)
        if len(hits) >= 2:
            break
    return hits


def latest_clusters(conn, domain, limit=25):
    return conn.execute(
        """SELECT c.* FROM clusters c
           WHERE c.domain_id=? AND c.batch_id=
             (SELECT MAX(batch_id) FROM clusters WHERE domain_id=?)
           ORDER BY (SELECT COUNT(*) FROM author_clusters ac WHERE ac.cluster_id=c.id) DESC
           LIMIT ?""", (domain, domain, limit)).fetchall()


def domain_researchers(conn, domain, min_papers, all_flag):
    """核心层 + 被标记作者，域内论文 ≥min_papers（--all-researchers 时全部核心）。"""
    ids = [r["id"] for r in conn.execute(
        """SELECT DISTINCT a.id FROM authors a
           WHERE a.tier='core' OR a.id IN
             (SELECT author_id FROM author_flags WHERE status!='dismissed')""")]
    out = []
    for aid in ids:
        n = conn.execute(
            """SELECT COUNT(DISTINCT pa.paper_id) n FROM paper_authors pa
               JOIN paper_domains pd ON pd.paper_id=pa.paper_id
               WHERE pa.author_id=? AND pd.domain_id=?""", (aid, domain)).fetchone()["n"]
        if n >= min_papers or all_flag:
            out.append((aid, n))
    return out


def export_domain(conn, out: Path, domain: str, min_papers: int, top_papers: int, all_r: bool):
    ddir = out / "directions"
    rdir = out / "researchers"
    pdir = out / "papers"
    edir = out / "events"
    for d in (ddir, rdir, pdir, edir):
        d.mkdir(parents=True, exist_ok=True)

    # ---------- 方向快照缓存（名称/内容/审阅） ----------
    direction_set = {c["id"] for c in latest_clusters(conn, domain)}   # 将导出方向（研究者的方向链接只指向这些）
    snap_cache = {}
    for c in latest_clusters(conn, domain, limit=500):
        snap = conn.execute(
            """SELECT content, review_status FROM snapshots WHERE cluster_id=?
               ORDER BY id DESC LIMIT 1""", (c["id"],)).fetchone()
        content, review = {}, None
        if snap and snap["content"]:
            try:
                content = json.loads(snap["content"])
            except json.JSONDecodeError:
                pass
            review = snap["review_status"]
        snap_cache[c["id"]] = {"name": c["name"] or content.get("name"), "content": content, "review": review}

    # ---------- 研究者（核心 + 被标记，域内论文达标） ----------
    n_res = 0
    res_by_dir = {}          # cid -> [(aid, name_display)]
    all_res_reps = {}        # aid -> [paper_id]（研究者代表作，并入论文导出）
    researcher_set = set()   # 已导出研究者（论文作者 wikilink 只指向这些）
    for aid, npapers in domain_researchers(conn, domain, min_papers, all_r):
        a = conn.execute(
            "SELECT name_display, name_zh, tier, orcid, openalex_id FROM authors WHERE id=?",
            (aid,)).fetchone()
        aff = conn.execute(
            """SELECT institution, source_tag, verified FROM affiliations
               WHERE author_id=? ORDER BY (verified=1) DESC, source_tag='web' DESC, start_year LIMIT 1""",
            (aid,)).fetchone()
        snap = conn.execute(
            """SELECT content, review_status FROM author_snapshots
               WHERE author_id=? AND status!='superseded' ORDER BY id DESC LIMIT 1""",
            (aid,)).fetchone()
        content, review = {}, "none"
        if snap and snap["content"]:
            try:
                content = json.loads(snap["content"])
            except json.JSONDecodeError:
                pass
            review = snap["review_status"] or "none"
        flags = [r["level"] for r in conn.execute(
            "SELECT level FROM author_flags WHERE author_id=? AND status!='dismissed'", (aid,))]
        # 方向归属：仅最新批次（与导出方向一一对应）
        dirs = [r["cluster_id"] for r in conn.execute(
            """SELECT ac.cluster_id FROM author_clusters ac
               JOIN clusters c ON c.id=ac.cluster_id
               WHERE ac.author_id=? AND c.domain_id=? AND c.batch_id=
                 (SELECT MAX(batch_id) FROM clusters WHERE domain_id=?)
               ORDER BY ac.weight DESC LIMIT 4""", (aid, domain, domain))]
        reps = [r["paper_id"] for r in conn.execute(
            """SELECT pa.paper_id FROM paper_authors pa
               JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
               WHERE pa.author_id=? ORDER BY (SELECT cited_by_count FROM papers p WHERE p.id=pa.paper_id) DESC
               LIMIT 3""", (domain, aid))]
        all_res_reps[aid] = reps
        researcher_set.add(aid)
        for cid in dirs:
            if cid in snap_cache:
                res_by_dir.setdefault(cid, []).append((aid, a["name_display"]))

        focus = content.get("focus", "")
        front = {
            "type": "researcher", "id": aid, "name": f'"{a["name_display"]}"',
            "name_zh": f'"{a["name_zh"] or ""}"',
            "tier": a["tier"], "domain": domain, "papers": npapers,
            "institution": f'"{aff["institution"]}"' if aff else '""',
            "inst_verified": (aff["verified"] if aff else 0),
            "orcid": f'"{a["orcid"] or ""}"' if a["orcid"] else '""',
            "flags": flags, "directions": dirs, "representative": reps,
            "focus": f'"{focus}"', "review": review,
        }
        # 正文：三层研究方向结构（确定性优先，LLM 画像仅已审展示）
        body = f"# {a['name_display']}" + (f"（{a['name_zh']}）" if a["name_zh"] else "") + "\n\n"
        body += f"> [!info] 概要\n> "
        parts = []
        if a["tier"] == "core":
            parts.append("核心层")
        if aff:
            parts.append(f"🏛 {aff['institution']}")
        parts.append(f"域内论文 **{npapers}**")
        if focus and review == "approved":
            parts.insert(0, f"🎯 {focus}")
        elif focus and review == "pending":
            parts.insert(0, f"🎯 {focus}（画像待审）")
        body += " · ".join(parts) + "\n\n"
        if content.get("summary") and review == "approved":
            body += content["summary"] + "\n\n"
        if content.get("key_contributions") and review == "approved":
            kc = content["key_contributions"]
            body += "**主要贡献**：" + (kc if isinstance(kc, str) else "；".join(kc)) + "\n\n"
        body += "**研究方向**\n"
        dir_links = [f"[[{slug_direction(cid)}|{snap_cache[cid]['name'] or f'方向 #{cid}'}]]"
                     for cid in dirs if cid in direction_set]
        if dir_links:
            body += "- 所属方向：" + "、".join(dir_links) + "\n"
        else:
            body += "- 所属方向：—（小簇，未纳入导出方向）\n"
        if reps:
            rep_titles = []
            for pid in reps:
                pf = conn.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone()
                rep_titles.append(f"[[{slug_paper(pid)}|{pf['title'][:40] if pf else pid}]]" if pf else f"[[{slug_paper(pid)}]]")
            body += "- 代表作：" + "、".join(rep_titles) + "\n"
        if flags:
            body += f"\n⚠️ 标记：{','.join(flags)}（详见事件笔记）\n"
        elif content.get("risks") and review == "approved":
            body += f"\n⚠️ {content['risks']}\n"
        md_file(rdir, slug_researcher(aid) + ".md", front, body)
        n_res += 1

    # ---------- 方向（方案 C：callout + details 折叠叙述 + 链接清单） ----------
    n_dir = 0
    dir_meta = {}   # cid -> {name, file}
    for c in latest_clusters(conn, domain):
        cid = c["id"]
        sc = snap_cache.get(cid, {})
        name = sc.get("name") or f"方向 #{c['label']}"
        content = sc.get("content", {})
        review = sc.get("review")
        years = Counter()
        for r in conn.execute(
                """SELECT p.year, COUNT(DISTINCT pa.paper_id) n FROM author_clusters ac
                   JOIN paper_authors pa ON pa.author_id=ac.author_id
                   JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
                   JOIN papers p ON p.id=pa.paper_id
                   WHERE ac.cluster_id=? AND p.year IS NOT NULL GROUP BY p.year""",
                (domain, cid)):
            years[r["year"]] = r["n"]
        linked = [x["c2"] for x in conn.execute(
            """SELECT ac2.cluster_id AS c2, COUNT(DISTINCT pa.paper_id) n
               FROM author_clusters ac1
               JOIN paper_authors pa ON pa.author_id=ac1.author_id
               JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
               JOIN author_clusters ac2 ON ac2.author_id=pa.author_id AND ac2.cluster_id!=ac1.cluster_id
               JOIN clusters c2 ON c2.id=ac2.cluster_id
               WHERE ac1.cluster_id=? AND c2.domain_id=? AND c2.batch_id=
                 (SELECT MAX(batch_id) FROM clusters WHERE domain_id=?)
               GROUP BY ac2.cluster_id HAVING n>=2 ORDER BY n DESC LIMIT 6""",
                (domain, cid, domain, domain))]
        size = conn.execute("SELECT COUNT(*) n FROM author_clusters WHERE cluster_id=?",
                            (cid,)).fetchone()["n"]
        st = conn.execute(
            """SELECT COUNT(DISTINCT pa.paper_id) n,
                      COUNT(DISTINCT CASE WHEN p.year>=2024 THEN pa.paper_id END) recent,
                      SUM((SELECT c FROM (SELECT p2.cited_by_count c FROM papers p2 WHERE p2.id=pa.paper_id))) cit
               FROM author_clusters ac JOIN paper_authors pa ON pa.author_id=ac.author_id
               JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
               JOIN papers p ON p.id=pa.paper_id WHERE ac.cluster_id=?""",
            (domain, cid)).fetchone()
        top = [r["name_display"] for r in conn.execute(
            """SELECT a.name_display, COUNT(DISTINCT pa.paper_id) np FROM author_clusters ac
               JOIN authors a ON a.id=ac.author_id
               LEFT JOIN paper_authors pa ON pa.author_id=a.id
               LEFT JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
               WHERE ac.cluster_id=? GROUP BY a.id ORDER BY np DESC LIMIT 5""",
                (domain, cid))]
        members = res_by_dir.get(cid, [])[:20]
        reps = [r["paper_id"] for r in conn.execute(
            """SELECT DISTINCT pa.paper_id FROM author_clusters ac
               JOIN paper_authors pa ON pa.author_id=ac.author_id
               JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
               JOIN papers p ON p.id=pa.paper_id
               WHERE ac.cluster_id=? ORDER BY p.cited_by_count DESC LIMIT 5""",
                (domain, cid))]
        front = {
            "type": "direction", "domain": domain, "direction_id": cid,
            "name": f'"{name}"', "size": size, "papers": st["n"] or 0,
            "recent": st["recent"] or 0, "citations": st["cit"] or 0,
            "review": review or "none", "label": c["label"],
            "years": dict(sorted(years.items())), "linked": linked,
            "top_authors": top,
        }
        body = f"# {name}\n\n"
        body += f"> [!summary] 方向概览\n> 规模 **{size}** 人 · 论文 **{st['n'] or 0}** · 近三年 **{st['recent'] or 0}** · 被引 **{st['cit'] or 0}**" + \
                (f" · 审阅：{review}" if review else "") + "\n\n"
        narr = ""
        if content.get("definition"):
            narr += f"## 研究概述\n\n{content['definition']}\n\n"
        if content.get("current_conclusions"):
            narr += f"## 当前结论\n\n{content['current_conclusions']}\n\n"
        if content.get("timeline"):
            narr += f"## 历史进程\n\n{content['timeline']}\n\n"
        if content.get("controversies"):
            narr += f"## 分歧与风险\n\n{content['controversies']}\n\n"
        if narr:
            body += "<details>\n<summary>📖 方向叙述（LLM 合成，待审）</summary>\n\n" + narr + "</details>\n\n"
        if members:
            body += "## 代表研究者\n\n" + "、".join(f"[[{aid}]]" for aid, _ in members) + "\n\n"
        if reps:
            rep_links = []
            for pid in reps:
                pf = conn.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone()
                rep_links.append(f"[[{slug_paper(pid)}|{pf['title'][:50] if pf else pid}]]" if pf else f"[[{slug_paper(pid)}]]")
            body += "## 代表论文\n\n" + "、".join(rep_links) + "\n"
        md_file(ddir, slug_direction(cid) + ".md", front, body)
        dir_meta[cid] = {"name": name, "file": slug_direction(cid)}
        n_dir += 1

    # ---------- 论文（P2：元数据 + 方向角色锚定句；集合 = 方向代表 ∪ 研究者代表作 ∪ 域高被引） ----------
    paper_ids = []
    seen = set()
    def _add(pid):
        if pid not in seen:
            seen.add(pid)
            paper_ids.append(pid)
    for cid in direction_set:
        for r in conn.execute(
                """SELECT DISTINCT pa.paper_id FROM author_clusters ac
                   JOIN paper_authors pa ON pa.author_id=ac.author_id
                   JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
                   JOIN papers p ON p.id=pa.paper_id
                   WHERE ac.cluster_id=? ORDER BY p.cited_by_count DESC LIMIT 5""",
                (domain, cid)):
            _add(r["paper_id"])
    for reps in all_res_reps.values():
        for pid in reps:
            _add(pid)
    # 域高被引补充（受 top_papers 约束；代表论文不受限，保证 wikilink 不悬空）
    for r in conn.execute(
            """SELECT p.id FROM papers p JOIN paper_domains pd ON pd.paper_id=p.id
               WHERE pd.domain_id=? ORDER BY p.cited_by_count DESC LIMIT ?""",
            (domain, top_papers)):
        _add(r["id"])

    paper_dir_roles = {}   # pid -> [(sentence, direction_name, cid)]
    for cid in direction_set:
        sc = snap_cache.get(cid, {})
        content = sc.get("content", {})
        dname = sc.get("name") or f"方向 #{cid}"
        for field in ("definition", "current_conclusions", "timeline", "controversies"):
            for pid in paper_ids:
                hits = find_paper_sentences(content.get(field, ""), pid)
                if hits:
                    paper_dir_roles.setdefault(pid, []).append((hits[0], dname, cid))
                    break  # 每个方向一节最多一句

    n_pap = 0
    for pid in paper_ids:
        p = conn.execute(
            "SELECT title, year, journal, doi, pmid, cited_by_count, retraction_status FROM papers WHERE id=?",
            (pid,)).fetchone()
        if not p:
            continue
        authors = [r["author_id"] for r in conn.execute(
            "SELECT author_id FROM paper_authors WHERE paper_id=? ORDER BY position LIMIT 8", (pid,))]
        dirs_of = [cid for cid in direction_set if conn.execute(
            """SELECT 1 FROM author_clusters ac JOIN paper_authors pa ON pa.author_id=ac.author_id
               WHERE ac.cluster_id=? AND pa.paper_id=? LIMIT 1""", (cid, pid)).fetchone()][:5]
        front = {
            "type": "paper", "paper_id": pid, "title": f'"{p["title"]}"',
            "year": p["year"] or "", "journal": f'"{p["journal"]}"',
            "doi": f'"{p["doi"] or ""}"', "pmid": f'"{p["pmid"] or ""}"',
            "cited": p["cited_by_count"] or 0, "retraction": p["retraction_status"],
            "authors": authors, "directions": dirs_of,
        }
        body = f"# {p['title']}\n\n"
        body += f"> {p['year'] or '?'} · {p['journal']} · 被引 **{p['cited_by_count'] or 0}**" + \
                (f" · ⚠️ {p['retraction_status']}" if p["retraction_status"] != "none" else "") + "\n\n"
        links = []
        if p["pmid"]:
            links.append(f"[PubMed](https://pubmed.ncbi.nlm.nih.gov/{p['pmid']}/)")
        if p["doi"]:
            links.append(f"DOI: {p['doi']}")
        if links:
            body += " · ".join(links) + "\n\n"
        if authors:
            names = {}
            for aid in authors:
                r = conn.execute("SELECT name_display FROM authors WHERE id=?", (aid,)).fetchone()
                if r:
                    names[aid] = r["name_display"]
            # 仅已导出研究者加 wikilink；其余纯文本（避免悬空链接）
            body += "**作者**：" + "、".join(
                f"[[{a}|{names[a]}]]" if a in researcher_set and a in names else (names.get(a) or a)
                for a in authors) + "\n\n"
        roles = paper_dir_roles.get(pid, [])
        if roles:
            body += "**方向角色**\n\n"
            for sent, dname, cid in roles[:2]:
                body += f"> {sent}\n>\n> — 出处：[[{slug_direction(cid)}|{dname}]] 方向快照\n\n"
        if dirs_of:
            body += "**所属方向**：" + "、".join(
                f"[[{slug_direction(cid)}|{snap_cache[cid]['name'] or f'方向 #{cid}'}]]" for cid in dirs_of) + "\n"
        md_file(pdir, slug_paper(pid) + ".md", front, body)
        n_pap += 1

    # ---------- 事件 ----------
    n_ev = 0
    for ev in conn.execute("SELECT * FROM fraud_events WHERE domain_id=?", (domain,)):
        srcs = json.loads(ev["source_urls"] or "[]")
        paper_flags = [{"paper_id": r["paper_id"], "title": r["title"], "flag_type": r["flag_type"]}
                       for r in conn.execute(
                           """SELECT pf.paper_id, p.title, pf.flag_type FROM paper_flags pf
                              JOIN papers p ON p.id=pf.paper_id WHERE pf.event_id=?""", (ev["id"],))]
        author_flags = [{"author_id": r["author_id"], "level": r["level"], "status": r["status"],
                         "basis": r["basis"] or ""}
                        for r in conn.execute(
                            """SELECT author_id, level, status, basis FROM author_flags
                               WHERE event_id=? ORDER BY level""", (ev["id"],))]
        front = {
            "type": "event", "event_id": ev["id"], "slug": ev["slug"],
            "title": f'"{ev["title"]}"', "status": ev["status"],
            "domain": ev["domain_id"], "source_urls": srcs,
            "paper_flags": paper_flags, "author_flags": author_flags,
        }
        body = f"# {ev['title']}\n\n> 状态：**{ev['status']}**\n\n{ev['description'] or ''}\n\n"
        for u in srcs:
            body += f"- {u}\n"
        if paper_flags:
            body += "\n**论文级标记**：\n" + "\n".join(
                f"- [[{slug_paper(x['paper_id'])}|{x['title'][:50]}]]（{x['flag_type']}）" for x in paper_flags) + "\n"
        if author_flags:
            body += "\n**人员级标记**：\n" + "\n".join(
                f"- [[{slug_researcher(x['author_id'])}]] {x['level']}（{x['status']}）{x['basis']}" for x in author_flags) + "\n"
        md_file(edir, slug_event(ev["id"]) + ".md", front, body)
        n_ev += 1

    return {"directions": n_dir, "researchers": n_res, "papers": n_pap, "events": n_ev}


def export_layout(conn, out: Path, domain: str):
    """导出最新布局批次 → <peopar>/_layout/<domain>.json（插件静态渲染信息化方向图谱）。"""
    batch = conn.execute(
        "SELECT MAX(batch_id) b FROM node_layout WHERE domain_id=?", (domain,)).fetchone()["b"]
    if not batch:
        print(f"[export-layout] {domain}: 无布局（先 analyze/layout.py {domain}）")
        return
    rows = conn.execute(
        "SELECT * FROM node_layout WHERE domain_id=? AND batch_id=? ORDER BY type", (domain, batch))
    data = {"domain": domain, "batch": batch, "directions": [], "papers": [], "authors": [], "edges": []}
    for r in rows:
        rec = {"id": r["id"], "x": r["x"], "y": r["y"], "r": r["r"],
               "cluster_id": r["cluster_id"], "affinity": r["affinity"]}
        if r["type"] == "direction":
            c = conn.execute("SELECT name FROM clusters WHERE id=?", (r["cluster_id"],)).fetchone()
            size = conn.execute("SELECT COUNT(*) n FROM author_clusters WHERE cluster_id=?",
                                (r["cluster_id"],)).fetchone()["n"]
            rec["name"] = c["name"] if c else None
            rec["size"] = size
            data["directions"].append(rec)
        elif r["type"] == "paper":
            p = conn.execute("SELECT title, cited_by_count FROM papers WHERE id=?",
                             (r["id"][2:],)).fetchone()
            if p:
                rec["title"] = p["title"]
                rec["cite"] = p["cited_by_count"]
                data["papers"].append(rec)
        else:
            a = conn.execute("SELECT name_display, name_zh FROM authors WHERE id=?", (r["id"],)).fetchone()
            if a:
                rec["name"] = a["name_display"]
                rec["zh"] = a["name_zh"]
                data["authors"].append(rec)
    auth = {a["id"]: a for a in data["authors"]}
    for a in auth:
        n = 0
        for p in data["papers"]:
            if p.get("cluster_id") != auth[a].get("cluster_id") or n >= 3:
                continue
            if conn.execute("SELECT 1 FROM paper_authors WHERE author_id=? AND paper_id=? LIMIT 1",
                            (a, p["id"][2:])).fetchone():
                data["edges"].append({"source": a, "target": p["id"], "kind": "authored"})
                n += 1
    ldir = out / "_layout"
    ldir.mkdir(parents=True, exist_ok=True)
    (ldir / f"{domain}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"[export-layout] {domain}: {len(data['directions'])} 方向 / {len(data['papers'])} 论文 / "
          f"{len(data['authors'])} 作者 → _layout/{domain}.json")


def main():
    ap = argparse.ArgumentParser(description="导出 vault 投影（<方向名>/peopar/ 目录 md 笔记）")
    ap.add_argument("vault", help="Obsidian 仓库根目录")
    ap.add_argument("--topic", default="神经语言学与失语症", help="研究大方向名（顶层目录名，如 神经语言学与失语症）")
    ap.add_argument("--domain", default="neuroling,ad_lesne", help="域列表，逗号分隔")
    ap.add_argument("--researcher-min-papers", type=int, default=3, help="研究者导出最小域内论文数")
    ap.add_argument("--top-papers", type=int, default=300, help="高被引论文导出上限")
    ap.add_argument("--all-researchers", action="store_true", help="导出全部核心层研究者（忽略 min-papers）")
    args = ap.parse_args()

    vault = Path(args.vault).expanduser()
    if not vault.is_dir():
        raise SystemExit(f"vault 不存在：{vault}")
    out = vault / args.topic / "peopar"
    out.mkdir(parents=True, exist_ok=True)

    conn = connect()
    init_db(conn)
    domains = [d.strip() for d in args.domain.split(",") if d.strip()]
    stats = {}
    for d in domains:
        if not conn.execute("SELECT id FROM domains WHERE id=?", (d,)).fetchone():
            print(f"[skip] 域不存在：{d}")
            continue
        st = export_domain(conn, out, d, args.researcher_min_papers, args.top_papers,
                           args.all_researchers)
        stats[d] = st
        print(f"[export] {d}: 方向 {st['directions']} / 研究者 {st['researchers']} / "
              f"论文 {st['papers']} / 事件 {st['events']}")
        export_layout(conn, out, d)
        md_file(out, f"{d}.peopar", {"domain": d, "type": "peopar-view", "topic": args.topic},
                f"# 百官行述 · {d}\n\n双击以插件视图打开（大方向：{args.topic}）。")
    md_file(out, "_sync.md",
            {"synced_at": datetime.now().isoformat(timespec="seconds"), "topic": args.topic, "domains": stats},
            "# 百官行述 · 同步状态\n\n本目录由 `python3 manage/export_vault.py --topic \"<方向名>\"` 生成，"
            "为 SQLite 权威数据的投影快照。\n\n文件规范见仓库 `doc/obsidian-vault-format.md`。\n")
    # 清理过期文件（本次未产出的旧 md）
    produced = {p.name for p in out.rglob("*.md")} | {p.name for p in out.rglob("*.peopar")}
    for p in out.rglob("*.md"):
        if p.name not in produced:
            p.unlink()
    conn.close()
    print(f"[export] 完成 → {out}")


if __name__ == "__main__":
    main()
