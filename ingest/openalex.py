"""OpenAlex 回填：作者身份解析（openalex author id 作归并线索）、机构履历、被引数；
以及种子引用扩散（cite_seed 规则）。仅标准库。"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import (audit, connect, ensure_domain, fetch_json, init_db,
                           name_sort, next_author_id, norm_pinyin, norm_title)

OA = "https://api.openalex.org"
MAILTO = "peopar@local"


def oa_get(path: str, params: dict | None = None):
    params = dict(params or {})
    params["mailto"] = MAILTO
    return fetch_json(f"{OA}{path}", params)


def resolve_author(conn, oa_author: dict) -> str:
    """按 openalex author id 查找或创建系统研究者，返回 BG ID。"""
    oa_id = oa_author.get("id", "")
    row = None
    if oa_id:
        row = conn.execute("SELECT id FROM authors WHERE openalex_id=?", (oa_id,)).fetchone()
    if row:
        return row["id"]
    aid = next_author_id(conn)
    display = oa_author.get("display_name") or "Unknown"
    conn.execute(
        """INSERT INTO authors(id, name_display, name_sort, pinyin_norm, openalex_id, orcid, tier)
           VALUES(?,?,?,?,?,?, 'peripheral')""",
        (aid, display, name_sort(display), norm_pinyin(display), oa_id or None, oa_author.get("orcid")))
    conn.execute(
        "INSERT OR IGNORE INTO author_aliases(author_id, alias, alias_type, source, confidence, verified) "
        "VALUES(?, ?, 'pinyin', 'openalex', 1.0, 1)", (aid, display))
    audit(conn, "system", "author.create", "author", aid,
          {"name": display, "openalex_id": oa_id})
    return aid


def aff_token(affiliation: str) -> str:
    """机构归一化短键（防跨人误并的辅助键）。"""
    import re
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (affiliation or "").lower())
    return t[:12]


def resolve_fallback(conn, slot, paper_year) -> str:
    """无 OpenAlex 线索时的兜底：拼音 + 机构短键；标记低置信度待人工复核。"""
    key = norm_pinyin(slot["raw_name"])
    aff = aff_token(slot["affiliation"] or "")
    row = None
    if aff:
        row = conn.execute(
            """SELECT a.id FROM authors a WHERE a.pinyin_norm=? AND a.note LIKE ?""",
            (key, f"%fbkey:{aff}%")).fetchone()
    if row:
        return row["id"]
    aid = next_author_id(conn)
    conn.execute(
        """INSERT INTO authors(id, name_display, name_sort, pinyin_norm, tier, note)
           VALUES(?,?,?, ?, 'peripheral', ?)""",
        (aid, slot["raw_name"], name_sort(slot["raw_name"]), key,
         f"fallback_merge fbkey:{aff or 'none'} low_confidence"))
    conn.execute(
        "INSERT OR IGNORE INTO author_aliases(author_id, alias, alias_type, source, confidence, verified) "
        "VALUES(?, ?, 'pinyin', 'pubmed', 0.5, 0)", (aid, slot["raw_name"]))
    audit(conn, "system", "author.create_fallback", "author", aid,
          {"name": slot["raw_name"], "aff_key": aff})
    return aid


def link_affiliations(conn, author_id, authorship, year):
    for inst in authorship.get("institutions", []) or []:
        name = inst.get("display_name")
        if not name:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO affiliations(author_id, institution, institution_norm,
               start_year, end_year, source_tag, confidence)
               VALUES(?,?,?,?,?, 'auto', 0.8)""",
            (author_id, name, norm_title(name), year, year))


def ingest_domain(domain_id: str, limit: int | None = None, verbose=True):
    conn = connect()
    init_db(conn)
    cfg = ensure_domain(conn, domain_id)
    # 1) 找到本域尚未回填 openalex_id 的论文
    q = """SELECT p.id, p.pmid, p.doi FROM papers p
           JOIN paper_domains pd ON pd.paper_id = p.id
           WHERE pd.domain_id=? AND p.openalex_id IS NULL"""
    rows = conn.execute(q + (" LIMIT ?" if limit else ""),
                        (domain_id, limit) if limit else (domain_id,)).fetchall()
    if verbose:
        print(f"[openalex] {domain_id}: 待回填 {len(rows)} 篇")
    oa_batch = cfg.get("openalex", {}).get("batch", 100)
    n_resolved, n_authors = 0, 0
    for i in range(0, len(rows), oa_batch):
        chunk = rows[i:i + oa_batch]
        # 优先 PMID 批量（管道符 OR）
        pmids = [r["pmid"] for r in chunk if r["pmid"]]
        works_by_key = {}
        if pmids:
            js = oa_get("/works", {"filter": "pmid:" + "|".join(pmids),
                                   "per-page": min(200, len(pmids)),
                                   "select": "id,ids,doi,title,publication_year,cited_by_count,authorships"})
            for w in js.get("results", []):
                pm = (w.get("ids", {}).get("pmid") or "").rstrip("/").rsplit("/", 1)[-1]
                works_by_key[pm] = w
        for r in chunk:
            w = works_by_key.get((r["pmid"] or "").strip())
            if w is None and r["doi"]:
                try:
                    w = oa_get(f"/works/https://doi.org/{r['doi']}")
                except Exception:
                    w = None
            if w is None:
                continue
            n_resolved += 1
            _backfill_paper(conn, r["id"], w)
            n_authors += _resolve_paper_authors(conn, r["id"], w)
        conn.commit()
        if verbose:
            print(f"[openalex] {min(i + oa_batch, len(rows))}/{len(rows)} 已解析 {n_resolved}")
        time.sleep(0.15)
    # 2) 种子引用扩散
    n_seed = _cite_seed_expand(conn, cfg, domain_id)
    audit(conn, "system", "ingest.openalex.done", "domain", domain_id,
          {"papers_backfilled": n_resolved, "author_links": n_authors, "cite_seed_added": n_seed})
    conn.commit()
    if verbose:
        print(f"[完成] 回填 {n_resolved} 篇，作者链接 {n_authors}，引用扩散新增 {n_seed}")
    return n_resolved


def _backfill_paper(conn, paper_id, w):
    doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
    conn.execute(
        """UPDATE papers SET openalex_id=?, cited_by_count=?,
           doi=COALESCE(NULLIF(doi,''), ?), updated_at=datetime('now') WHERE id=?""",
        (w.get("id"), w.get("cited_by_count") or 0, doi, paper_id))


def _resolve_paper_authors(conn, paper_id, w) -> int:
    slots = conn.execute(
        "SELECT * FROM paper_author_staging WHERE paper_id=? ORDER BY position", (paper_id,)).fetchall()
    authorships = w.get("authorships", []) or []
    year = w.get("publication_year")
    linked = 0
    used = set()
    # 等长时按序配对（最可靠）
    pairs = []
    if len(slots) == len(authorships):
        pairs = list(zip(slots, authorships))
    else:
        # 按规范化姓名贪心配
        avail = list(enumerate(authorships))
        for s in slots:
            for j, (idx, ash) in enumerate(avail):
                dn = ash.get("author", {}).get("display_name", "")
                if norm_pinyin(dn) == s["pinyin_norm"] and idx not in used:
                    pairs.append((s, ash))
                    used.add(idx)
                    avail.pop(j)
                    break
    for s, ash in pairs:
        oa_author = ash.get("author", {})
        if not oa_author.get("id"):
            continue
        aid = resolve_author(conn, oa_author)
        link_affiliations(conn, aid, ash, year)
        conn.execute(
            "INSERT OR IGNORE INTO paper_authors(paper_id, author_id, position, raw_name) VALUES(?,?,?,?)",
            (paper_id, aid, s["position"], s["raw_name"]))
        conn.execute(
            "UPDATE paper_author_staging SET resolved_author_id=? WHERE paper_id=? AND position=?",
            (aid, paper_id, s["position"]))
        if oa_author.get("orcid"):
            conn.execute("UPDATE authors SET orcid=COALESCE(NULLIF(orcid,''), ?) WHERE id=?",
                         (oa_author["orcid"], aid))
        linked += 1
    # 兜底：未配对槽位
    resolved_pos = {s["position"] for s, _ in pairs}
    for s in slots:
        if s["position"] in resolved_pos:
            continue
        aid = resolve_fallback(conn, s, year)
        conn.execute(
            "INSERT OR IGNORE INTO paper_authors(paper_id, author_id, position, raw_name) VALUES(?,?,?,?)",
            (paper_id, aid, s["position"], s["raw_name"]))
        conn.execute(
            "UPDATE paper_author_staging SET resolved_author_id=? WHERE paper_id=? AND position=?",
            (aid, paper_id, s["position"]))
    return linked


def _cite_seed_expand(conn, cfg, domain_id) -> int:
    """种子引用扩散：引用了种子论文的文献按 cite_seed 规则收录。"""
    from ingest.pubmed import fetch_and_store_pmids
    seeds = cfg.get("seeds", {})
    cap = cfg.get("openalex", {}).get("max_cite_seed_per_seed", 500)
    seed_works = []
    for doi in seeds.get("seed_dois", []):
        try:
            w = oa_get(f"/works/https://doi.org/{doi}")
            seed_works.append((w.get("id"), doi))
        except Exception as e:
            print(f"[seed] DOI 解析失败 {doi}: {e}")
    for pmid in seeds.get("seed_pmids", []):
        try:
            js = oa_get("/works", {"filter": f"pmid:{pmid}", "select": "id"})
            res = js.get("results", [])
            if res:
                seed_works.append((res[0]["id"], pmid))
        except Exception as e:
            print(f"[seed] PMID 解析失败 {pmid}: {e}")
    if not seed_works:
        return 0
    citing_pmids = set()
    for sw_id, label in seed_works:
        try:
            got, cur = 0, "*"
            while got < cap and cur:
                js = oa_get("/works", {"filter": f"cites:{sw_id}", "per-page": 200,
                                       "cursor": cur, "select": "ids"})
                for w in js.get("results", []):
                    pm = (w.get("ids", {}).get("pmid") or "").rstrip("/").rsplit("/", 1)[-1]
                    if pm:
                        citing_pmids.add(pm)
                got += len(js.get("results", []))
                cur = js.get("meta", {}).get("next_cursor")
                time.sleep(0.15)
            print(f"[seed] {label} ({sw_id}): 被引抓取 {got} 条")
        except Exception as e:
            print(f"[seed] 被引检索失败 {label}: {e}")
    # 排除已收录
    existing = {r["pmid"] for r in conn.execute(
        "SELECT pmid FROM papers WHERE pmid IS NOT NULL").fetchall()}
    new_pmids = [p for p in citing_pmids if p not in existing]
    if new_pmids:
        fetch_and_store_pmids(conn, cfg, domain_id, new_pmids, rule="cite_seed")
    # 写引用边：施引论文 → 种子论文
    seed_paper_ids = []
    for doi in seeds.get("seed_dois", []):
        r = conn.execute("SELECT id FROM papers WHERE doi=?", (doi,)).fetchone()
        if r:
            seed_paper_ids.append(r["id"])
    for pmid in seeds.get("seed_pmids", []):
        r = conn.execute("SELECT id FROM papers WHERE pmid=?", (pmid,)).fetchone()
        if r:
            seed_paper_ids.append(r["id"])
    n_edges = 0
    for sp in seed_paper_ids:
        for pm in citing_pmids:
            r = conn.execute("SELECT id FROM papers WHERE pmid=?", (pm,)).fetchone()
            if r:
                conn.execute("INSERT OR IGNORE INTO citations(citing_id, cited_id, source) VALUES(?,?, 'openalex_cites')",
                             (r["id"], sp))
                n_edges += 1
    if seed_paper_ids:
        print(f"[seed] 引用边写入 {n_edges} 条")
    return len(new_pmids)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="OpenAlex 回填与作者解析")
    ap.add_argument("domain")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    ingest_domain(args.domain, args.limit)
