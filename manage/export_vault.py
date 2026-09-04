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


MANUAL_FM_RE = re.compile(r"^---\n(.*?)\n---", re.S)
MANUAL_DETAILS = re.compile(r"<details[^>]*class=\"pp-note\"[^>]*>.*?</details>", re.S)


def extract_manual(old_text: str) -> tuple[list, str]:
    """抽取人工修订：frontmatter 的 manual_* 键行 + 正文「📝 人工批注」details 区块（导出不覆盖）。"""
    fm_lines = []
    m = MANUAL_FM_RE.match(old_text)
    if m:
        fm_lines = [ln for ln in m.group(1).splitlines() if ln.strip().startswith("manual_")]
    body = ""
    mm = MANUAL_DETAILS.search(old_text)
    if mm:
        inner = re.sub(r"<details[^>]*>|<summary>.*?</summary>|</details>", "", mm.group(0), flags=re.S)
        body = inner.strip()
    return fm_lines, body


def manual_block(note: str) -> str:
    return f"\n<details class=\"pp-note\"><summary>📝 人工批注（本区导出不覆盖）</summary>\n\n{note}\n\n</details>\n"


def write_md(out: Path, name: str, front: dict, body: str):
    """写 md，保留既有文件中的 manual_* frontmatter 与人工批注 details 区块。"""
    fm_lines, manual_body = [], ""
    if (out / name).exists():
        fm_lines, manual_body = extract_manual((out / name).read_text(encoding="utf-8"))
    text = fm(front) + "\n\n" + body
    if manual_body:
        text += manual_block(manual_body)
    if fm_lines:
        text = re.sub(r"^(---\n)", r"\1" + "\n".join(fm_lines) + "\n", text, count=1)
    (out / name).write_text(text, encoding="utf-8")


def md_file(out: Path, name: str, front: dict, body: str):
    write_md(out, name, front, body)


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
            rep_items = []
            for pid in reps:
                pf = conn.execute("SELECT pmid, title, year FROM papers WHERE id=?", (pid,)).fetchone()
                if not pf:
                    continue
                item = f"{pf['title'][:46]}（{pf['year'] or '?'}）"
                if pf["pmid"]:
                    item += f" [PubMed](https://pubmed.ncbi.nlm.nih.gov/{pf['pmid']}/)"
                rep_items.append(item)
            if rep_items:
                body += "- 代表作：" + "；".join(rep_items) + "\n"
        # 论文清单（vault 可见，避免仅 DB 感知丢失）
        pa_rows = conn.execute(
            """SELECT p.title, p.year, p.cited_by_count, p.pmid FROM paper_authors pa
               JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
               JOIN papers p ON p.id=pa.paper_id WHERE pa.author_id=?
               ORDER BY p.cited_by_count DESC LIMIT 60""", (domain, aid)).fetchall()
        if pa_rows:
            body += "## 论文（" + str(npapers) + "）\n\n"
            for i, pp in enumerate(pa_rows[:40]):
                line = f"{i + 1}. {pp['title']}（{pp['year'] or '?'} · 被引 {pp['cited_by_count'] or 0}）"
                if pp["pmid"]:
                    line += f" [PubMed](https://pubmed.ncbi.nlm.nih.gov/{pp['pmid']}/)"
                body += line + "\n"
            if len(pa_rows) > 40:
                body += f"…（其余 {npapers - 40} 篇见数据库/图谱）\n"
            body += "\n"
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
        def arrow_timeline(txt: str) -> str:
            out = []
            for sent in re.split(r"(?<=[。；;\n])", txt or ""):
                sent = sent.strip()
                if not sent:
                    continue
                m = re.search(r"((?:19|20)\d{2})", sent)
                out.append(("▸ **" + m.group(1) + "** → " + sent) if m else ("▸ " + sent))
            return "\n".join(out)
        body = f"# {name}\n\n"
        body += f"> [!summary] 方向概览\n> 规模 **{size}** 人 · 论文 **{st['n'] or 0}** · 近三年 **{st['recent'] or 0}** · 被引 **{st['cit'] or 0}**" + \
                (f" · 审阅：{review}" if review else "") + "\n\n"
        if content.get("name") or name:
            pass
        if content.get("current_conclusions"):
            body += "## 🎯 当前结论\n\n> **" + content["current_conclusions"] + "**\n\n"
        if content.get("timeline"):
            body += "## 📜 历史进程\n\n" + arrow_timeline(content["timeline"]) + "\n\n"
        if content.get("definition"):
            body += "## 📖 研究概述\n\n" + content["definition"] + "\n\n"
        if content.get("controversies"):
            body += "## ⚖️ 分歧与风险\n\n" + content["controversies"] + "\n\n"
        if members:
            body += "## 代表研究者\n\n"
            for aid, nm in members[:20]:
                body += f"- [[{aid}|{nm}]]\n"
            body += "\n"
        if reps:
            body += "## 代表论文\n\n"
            for pid in reps:
                pf = conn.execute("SELECT pmid, title, year, abstract FROM papers WHERE id=?", (pid,)).fetchone()
                if not pf:
                    continue
                body += f"- **{pf['title']}**（{pf['year'] or '?'}）"
                ab = (pf["abstract"] or "").strip()
                if ab:
                    body += " — " + ab[:110] + ("…" if len(ab) > 110 else "")
                if pf["pmid"]:
                    body += f" [PubMed](https://pubmed.ncbi.nlm.nih.gov/{pf['pmid']}/)"
                body += "\n"
        md_file(ddir, slug_direction(cid) + ".md", front, body)
        dir_meta[cid] = {"name": name, "file": slug_direction(cid)}
        n_dir += 1

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
                f"- {x['title'][:60]}（{x['flag_type']}）" for x in paper_flags) + "\n"
        if author_flags:
            body += "\n**人员级标记**：\n" + "\n".join(
                f"- [[{slug_researcher(x['author_id'])}]] {x['level']}（{x['status']}）{x['basis']}" for x in author_flags) + "\n"
        md_file(edir, slug_event(ev["id"]) + ".md", front, body)
        n_ev += 1

    return {"directions": n_dir, "researchers": n_res, "papers": 0, "events": n_ev}


def export_layout(conn, out: Path, domain: str):
    """导出布局 JSON → <peopar>/_layout/<domain>.json（插件信息化方向图谱数据源）。"""
    ldir = out / "_layout"
    ldir.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent.parent / "data" / f"layout_{domain}.json"
    if src.exists():
        (ldir / f"{domain}.json").write_bytes(src.read_bytes())
        d = json.loads(src.read_text(encoding="utf-8"))
        print(f"[export-layout] {domain}: 复制 layout JSON（方向 {len(d.get('directions', []))} / "
              f"论文 {len(d.get('papers', []))} / 作者 {len(d.get('authors', []))}）")
        return
    batch = conn.execute(
        "SELECT MAX(batch_id) b FROM node_layout WHERE domain_id=?", (domain,)).fetchone()["b"]
    if batch:
        print(f"[export-layout] {domain}: 无 data/layout_{domain}.json（先 python3 analyze/layout.py {domain} "
              f"--out data/layout_{domain}.json）")
    else:
        print(f"[export-layout] {domain}: 无布局")


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
    # 清理：论文改为 DB 存储，删除遗留 papers/ 目录；其余子目录保留
    stale = out / "papers"
    if stale.is_dir():
        import shutil
        shutil.rmtree(stale)
        print("[export] 已删除遗留 papers/ 目录（论文改存 DB）")
    conn.close()
    print(f"[export] 完成 → {out}")


if __name__ == "__main__":
    main()
