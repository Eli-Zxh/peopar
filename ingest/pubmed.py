"""PubMed 采集器（E-utilities，仅标准库）。

确定性收录规则（本地复核，可复现）：
  - mesh      : 论文 MeSH 命中域配置词表
  - keyword   : 标题/摘要命中强关键词
  - author    : 范例域作者检索命中
  - cite_seed : 种子引用扩散（在 openalex.py 阶段）
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import (audit, connect, ensure_domain, fetch_json, fetch_xml,
                           init_db, norm_pinyin, norm_title)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BATCH = 200


def build_query(cfg: dict) -> str:
    p = cfg["pubmed"]
    parts = []
    if p.get("mesh_terms"):
        parts.append("(" + " OR ".join(f'"{t}"[MeSH Terms]' for t in p["mesh_terms"]) + ")")
    co = p.get("cooccur_terms") or []
    co_str = "(" + " OR ".join(f"{c}[tiab]" for c in co) + ")" if co else ""
    if p.get("mesh_boundary"):
        mb = "(" + " OR ".join(f'"{t}"[MeSH Terms]' for t in p["mesh_boundary"]) + ")"
        parts.append(f"({mb} AND {co_str})" if co_str else mb)
    kw = []
    for k in p.get("keywords_strong", []):
        kw.append(k if "[" in k else f"{k}[tiab]")
    if kw:
        parts.append("(" + " OR ".join(kw) + ")")
    if p.get("keywords_boundary"):
        kb = "(" + " OR ".join(f"{k}[tiab]" for k in p["keywords_boundary"]) + ")"
        parts.append(f"({kb} AND {co_str})" if co_str else kb)
    if p.get("author_queries"):
        parts.append("(" + " OR ".join(p["author_queries"]) + ")")
    return " OR ".join(parts)


def esearch_ids(term: str, max_fetch: int, sort: str, api_key: str | None):
    ids, retstart = [], 0
    count = None
    while True:
        params = {"db": "pubmed", "term": term, "retmax": min(10000, max_fetch - len(ids)),
                  "retstart": retstart, "retmode": "json"}
        if sort == "pub_date":
            params["sort"] = "pub_date"
        if api_key:
            params["api_key"] = api_key
        js = fetch_json(f"{EUTILS}/esearch.fcgi", params)
        res = js.get("esearchresult", {})
        if count is None:
            count = int(res.get("count", 0))
            print(f"[esearch] 命中总数 {count}，计划获取 {min(count, max_fetch)}")
        chunk = res.get("idlist", [])
        if not chunk:
            break
        ids.extend(chunk)
        retstart += len(chunk)
        if len(ids) >= min(count, max_fetch):
            break
        time.sleep(0.4)
    return ids[:max_fetch], count


def _itertext(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_article(art):
    """解析单个 PubmedArticle → dict"""
    mc = art.find("MedlineCitation")
    if mc is None:
        return None
    pmid = _itertext(mc.find("PMID"))
    article = mc.find("Article")
    if article is None:
        return None
    title = _itertext(article.find("ArticleTitle"))
    if not title:
        return None
    # 摘要
    abs_parts = []
    ab = article.find("Abstract")
    if ab is not None:
        for at in ab.findall("AbstractText"):
            label = at.get("Label")
            txt = _itertext(at)
            abs_parts.append(f"{label}: {txt}" if label else txt)
    # 期刊与日期
    j = article.find("Journal")
    journal, volume, pages, year, pub_date = "", "", "", None, ""
    if j is not None:
        journal = _itertext(j.find("Title")) or _itertext(j.find("ISOAbbreviation"))
        ji = j.find("JournalIssue")
        if ji is not None:
            volume = _itertext(ji.find("Volume"))
            pd = ji.find("PubDate")
            if pd is not None:
                y = _itertext(pd.find("Year"))
                if y.isdigit():
                    year = int(y)
                else:
                    m = re.search(r"(19|20)\d{2}", _itertext(pd.find("MedlineDate")))
                    if m:
                        year = int(m.group(0))
                med = _itertext(pd.find("Month"))
                day = _itertext(pd.find("Day"))
                pub_date = " ".join(x for x in [y or str(year or ""), med, day] if x)
    pg = article.find("Pagination/MedlinePgn")
    if pg is not None:
        pages = _itertext(pg)
    lang = _itertext(article.find("Language"))
    # 标识符
    doi = ""
    for aid in article.findall("ArticleIdList/ArticleId") + mc.findall(".//ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = _itertext(aid).lower()
        elif aid.get("IdType") == "pmc":
            pass
    # 出版物类型
    pub_types = [_itertext(pt) for pt in article.findall("PublicationTypeList/PublicationType")]
    # MeSH
    mesh = [_itertext(mh.find("DescriptorName")) for mh in mc.findall("MeshHeadingList/MeshHeading")]
    # 作者
    authors = []
    for pos, a in enumerate(article.findall("AuthorList/Author"), 1):
        last = _itertext(a.find("LastName"))
        fore = _itertext(a.find("ForeName"))
        initials = _itertext(a.find("Initials"))
        collective = _itertext(a.find("CollectiveName"))
        if not last and not collective:
            continue
        raw = f"{last} {initials}".strip() if last else collective
        aff = _itertext(a.find("AffiliationInfo/Affiliation"))
        authors.append({"position": pos, "raw_name": raw, "full_name": f"{last} {fore}".strip(),
                        "pinyin_norm": norm_pinyin(raw), "affiliation": aff})
    # 撤稿状态（以 PubMed 权威标注为准）
    ret_status = "none"
    pt_lower = {t.lower() for t in pub_types}
    if "retracted publication" in pt_lower:
        ret_status = "retracted"
    elif "published erratum" in pt_lower:
        ret_status = "corrected"
    # CommentsCorrections 中的撤稿关联
    retr_of = []
    for cc in mc.findall(".//CommentsCorrections"):
        if cc.get("RefType") in ("RetractionOf", "RetractionIn"):
            retr_of.append({"reftype": cc.get("RefType"), "pmid": _itertext(cc.find("PMID"))})
    return {
        "pmid": pmid, "doi": doi, "title": title, "year": year, "pub_date": pub_date,
        "journal": journal, "volume": volume, "pages": pages, "language": lang,
        "abstract": " ".join(abs_parts), "pub_types": pub_types, "mesh": mesh,
        "authors": authors, "retraction_status": ret_status, "retraction_links": retr_of,
    }


def local_rule_match(rec: dict, cfg: dict) -> tuple[str, str] | None:
    """本地复核收录规则，返回 (rule, matched_term)。"""
    p = cfg["pubmed"]
    mesh_all = set(p.get("mesh_terms", [])) | set(p.get("mesh_boundary", []))
    hit = [m for m in rec["mesh"] if m in mesh_all]
    if hit:
        return "mesh", hit[0]
    text = (rec["title"] + " " + rec["abstract"]).lower()
    for k in p.get("keywords_strong", []):
        word = re.sub(r"\[[^\]]*\]$", "", k).replace("*", "")
        if word.lower() in text:
            return "keyword", k
    return None


def fetch_and_store_pmids(conn, cfg, domain_id, pmids, rule="cite_seed", matched="seed_expansion"):
    """按 PMID 列表抓取入库，收录规则直接取传入值（引用扩散/事件链场景）。"""
    new_p, upd_p = 0, 0
    for i in range(0, len(pmids), BATCH):
        chunk = pmids[i:i + BATCH]
        root = fetch_xml(f"{EUTILS}/efetch.fcgi",
                         {"db": "pubmed", "retmode": "xml", "id": ",".join(chunk)})
        for art in root.findall("PubmedArticle"):
            rec = parse_article(art)
            if not rec:
                continue
            new_p, upd_p = _upsert_paper(conn, rec, domain_id, rule, matched, new_p, upd_p)
        conn.commit()
        time.sleep(0.4)
    print(f"[efetch:{rule}] 新增 {new_p} 更新 {upd_p}")
    return new_p


def ingest(domain_id: str, limit: int | None = None, api_key: str | None = None, dry_run=False,
           incremental=False):
    conn = connect()
    init_db(conn)
    cfg = ensure_domain(conn, domain_id)
    term = build_query(cfg)
    if incremental:
        row = conn.execute("SELECT cursor_value FROM cursors WHERE domain_id=? AND source='pubmed'",
                           (domain_id,)).fetchone()
        last = None
        if row:
            try:
                last = json.loads(row["cursor_value"]).get("date")
            except Exception:
                pass
        if not last:
            from datetime import date, timedelta
            last = (date.today() - timedelta(days=30)).isoformat()
        term = f"({term}) AND (\"{last}\"[EDAT] : \"3000\"[EDAT])"
        print(f"[增量] EDAT 窗口：{last} → 今")
    print(f"[query] {term}")
    if dry_run:
        print("[dry-run] 仅显示检索式，不执行采集")
        return
    max_fetch = limit or cfg["pubmed"].get("max_fetch", 10000)
    ids, total = esearch_ids(term, max_fetch, cfg["pubmed"].get("sort", ""), api_key)
    audit(conn, "system", "ingest.pubmed.search", "domain", domain_id,
          {"query": term, "total": total, "fetch": len(ids)})
    conn.commit()
    author_only = bool(cfg["pubmed"].get("author_queries"))
    new_p, upd_p = 0, 0
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        params = {"db": "pubmed", "retmode": "xml", "id": ",".join(chunk)}
        if api_key:
            params["api_key"] = api_key
        root = fetch_xml(f"{EUTILS}/efetch.fcgi", params)
        for art in root.findall("PubmedArticle"):
            rec = parse_article(art)
            if not rec:
                continue
            rule_match = local_rule_match(rec, cfg)
            if rule_match:
                rule, matched = rule_match
            elif author_only:
                rule, matched = "author", ";".join(cfg["pubmed"]["author_queries"])
            else:
                continue  # 本地复核不通过则不收（确定性、可审计）
            new_p, upd_p = _upsert_paper(conn, rec, domain_id, rule, matched, new_p, upd_p)
        conn.commit()
        print(f"[efetch] {min(i + BATCH, len(ids))}/{len(ids)}  新增 {new_p} 更新 {upd_p}")
        time.sleep(0.4)
    conn.execute("DELETE FROM cursors WHERE domain_id=? AND source='pubmed'", (domain_id,))
    conn.execute("INSERT INTO cursors(domain_id, source, cursor_value) VALUES(?,?,?)",
                 (domain_id, "pubmed",
                  json.dumps({"query": term, "fetched": len(ids),
                              "date": time.strftime("%Y/%m/%d")}, ensure_ascii=False)))
    audit(conn, "system", "ingest.pubmed.done", "domain", domain_id,
          {"new": new_p, "updated": upd_p})
    conn.commit()
    print(f"[完成] {domain_id}: 新增 {new_p} 篇，更新 {upd_p} 篇")


def _upsert_paper(conn, rec, domain_id, rule, matched, new_p, upd_p):
    t_norm = norm_title(rec["title"])
    fa_norm = rec["authors"][0]["pinyin_norm"] if rec["authors"] else ""
    row = None
    if rec["pmid"]:
        row = conn.execute("SELECT id FROM papers WHERE pmid=?", (rec["pmid"],)).fetchone()
    if row is None and rec["doi"]:
        row = conn.execute("SELECT id FROM papers WHERE doi=?", (rec["doi"],)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id FROM papers WHERE title_norm=? AND first_author_norm=? AND year=?",
            (t_norm, fa_norm, rec["year"])).fetchone()
    vals = dict(doi=rec["doi"] or None, pmid=rec["pmid"] or None, title=rec["title"],
                title_norm=t_norm, year=rec["year"], pub_date=rec["pub_date"],
                journal=rec["journal"], volume=rec["volume"], pages=rec["pages"],
                abstract=rec["abstract"], pub_types=json.dumps(rec["pub_types"], ensure_ascii=False),
                mesh=json.dumps(rec["mesh"], ensure_ascii=False), language=rec["language"],
                retraction_status=rec["retraction_status"], first_author_norm=fa_norm,
                updated_at="datetime('now')")
    if row:
        pid = row["id"]
        sets = ", ".join(f"{k}=?" for k in vals if k != "updated_at")
        conn.execute(f"UPDATE papers SET {sets}, updated_at=datetime('now') WHERE id=?",
                     [v for k, v in vals.items() if k != "updated_at"] + [pid])
        upd_p += 1
    else:
        cur = conn.execute(
            f"""INSERT INTO papers(doi, pmid, title, title_norm, year, pub_date, journal, volume,
               pages, abstract, pub_types, mesh, language, retraction_status, first_author_norm)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vals["doi"], vals["pmid"], vals["title"], vals["title_norm"], vals["year"],
             vals["pub_date"], vals["journal"], vals["volume"], vals["pages"], vals["abstract"],
             vals["pub_types"], vals["mesh"], vals["language"], vals["retraction_status"], fa_norm))
        pid = cur.lastrowid
        new_p += 1
    conn.execute("INSERT OR IGNORE INTO paper_domains(paper_id, domain_id, rule, matched_term) VALUES(?,?,?,?)",
                 (pid, domain_id, rule, matched))
    # 作者暂存槽位
    for a in rec["authors"]:
        conn.execute(
            """INSERT OR IGNORE INTO paper_author_staging(paper_id, position, raw_name, pinyin_norm, affiliation)
               VALUES(?,?,?,?,?)""",
            (pid, a["position"], a["raw_name"], a["pinyin_norm"], a["affiliation"]))
    # PubMed 撤稿标注 → 自动登记论文级标记（无事件归属时 event_id 为 NULL，待事件登记后挂接）
    if rec["retraction_status"] == "retracted":
        conn.execute(
            """INSERT INTO paper_flags(paper_id, flag_type, note, created_by)
               SELECT ?, 'retraction', 'PubMed 标注为 Retracted Publication', 'system'
               WHERE NOT EXISTS (SELECT 1 FROM paper_flags WHERE paper_id=? AND flag_type='retraction' AND created_by='system')""",
            (pid, pid))
    return new_p, upd_p


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PubMed E-utilities 采集器")
    ap.add_argument("domain", help="域 ID（config/domains/*.json）")
    ap.add_argument("--limit", type=int, help="覆盖配置的最大获取数")
    ap.add_argument("--api-key", help="NCBI API Key（可选，提高速率限制）")
    ap.add_argument("--dry-run", action="store_true", help="仅打印检索式")
    ap.add_argument("--incremental", action="store_true",
                    help="增量模式：仅检索上次运行后入库的记录（EDAT 窗口）")
    args = ap.parse_args()
    ingest(args.domain, args.limit, args.api_key, args.dry_run, args.incremental)
