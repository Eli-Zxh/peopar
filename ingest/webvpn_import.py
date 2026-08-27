"""机构 webvpn 数据导入器：Scopus CSV / 通用 RIS（CNKI 知网、万方、EndNote）。

定位：B 层订阅/中文源接入。工作方式为 skill 式半自动——
用户在机构 webvpn 门户（已登录）检索后导出标准题录文件，AI 助手或用户执行本模块入库。
本模块只做确定性解析与收录，不调用任何 LLM；收录判定映射为 keyword 规则
（检索本质是用户在源站执行的关键词检索，matched_term 记录 webvpn 来源与检索式）。

用法：
  python3 ingest/webvpn_import.py <file> --source scopus|ris --domain <域id> [--query "检索式"] [--dry-run]
"""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, ensure_domain, init_db, name_sort, next_author_id, norm_pinyin, norm_title

HANZI = re.compile(r"[\u4e00-\u9fff]")


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:24]


# ---------- Scopus CSV 解析 ----------

SCOPUS_FIELDS = {
    "author full names": "authors_full",
    "author(s) id": "author_ids",
    "title": "title",
    "year": "year",
    "doi": "doi",
    "source title": "journal",
    "affiliations": "affiliations",
    "cited by": "cited_by",
    "abstract": "abstract",
    "volume": "volume",
    "issue": "issue",
    "art. no.": "pages",
}


def parse_scopus_csv(text: str) -> list[dict]:
    """Scopus CSV 导出 → 统一记录列表。"""
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        if not row:
            continue
        rec = {}
        for k, v in row.items():
            key = (k or "").strip().lower()
            if key in SCOPUS_FIELDS:
                rec[SCOPUS_FIELDS[key]] = (v or "").strip()
        title = rec.get("title", "")
        if not title:
            continue
        year = rec.get("year", "")
        try:
            year = int(re.sub(r"\D", "", year)[:4]) if year else None
        except (ValueError, IndexError):
            year = None
        authors = []
        # "Zhang, X.; Li, Y." 或 "Zhang X., Li Y." 形式
        full = rec.get("authors_full", "")
        for part in re.split(r"[;；]", full):
            part = part.strip()
            if not part:
                continue
            # Scopus 惯用 "姓, 名首字母"：倒序成 "名 姓" 与库内 display 对齐
            if "," in part:
                last, fore = part.rsplit(",", 1)
                disp = f"{fore.strip()} {last.strip()}".strip()
            else:
                disp = part
            authors.append({"position": len(authors) + 1, "raw_name": disp,
                            "pinyin_norm": norm_pinyin(disp)})
        rec.update({
            "year": year,
            "journal": rec.get("journal", ""),
            "doi": (rec.get("doi", "") or "").lower(),
            "authors": authors,
            "retraction_status": "none",
            "pub_types": [],
            "mesh": [],
            "language": "en",
            "volume": rec.get("volume", ""),
            "pages": rec.get("pages", ""),
            "abstract": rec.get("abstract", ""),
        })
        out.append(rec)
    return out


# ---------- RIS 通用解析（CNKI NoteExpress / 万方 / EndNote） ----------

def parse_ris(text: str) -> list[dict]:
    """RIS 题录 → 统一记录列表。CNKI 与万方导出的 RIS 字段略有差异，做兼容。"""
    records, cur = [], None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        tag, _, val = line.partition("  - ") if "  - " in line else line.partition(" - ")
        tag = tag.strip().upper()
        val = val.strip()
        if tag == "TY":
            cur = {"type": val, "authors": [], "mesh": [], "pub_types": [], "retraction_status": "none",
                   "language": "", "abstract": "", "volume": "", "pages": "", "affiliations": ""}
            records.append(cur)
            continue
        if cur is None:
            continue
        if tag in ("T1", "TI", "CT", "BT"):
            cur["title"] = cur.get("title", "") + val
        elif tag == "AU":
            # CNKI 作者为汉字名；Scopus/EndNote 为 "姓, 名" 或 "名 姓"
            name = val.strip()
            if "," in name and not HANZI.search(name):
                last, fore = name.rsplit(",", 1)
                name = f"{fore.strip()} {last.strip()}".strip()
            cur["authors"].append({"position": len(cur["authors"]) + 1, "raw_name": name,
                                   "pinyin_norm": norm_pinyin(name)})
        elif tag == "PY":
            m = re.search(r"(19|20)\d{2}", val)
            cur["year"] = int(m.group(0)) if m else None
        elif tag in ("JF", "T2", "JO", "JA"):
            cur["journal"] = cur.get("journal", "") + val
        elif tag == "DO":
            cur["doi"] = val.lower()
        elif tag in ("AB", "N2"):
            cur["abstract"] = (cur.get("abstract", "") + " " + val).strip()
        elif tag in ("AD", "AF", "C1"):
            cur["affiliations"] = (cur.get("affiliations", "") + " " + val).strip()
        elif tag == "VL":
            cur["volume"] = val
        elif tag in ("SP", "EP"):
            cur["pages"] = (cur.get("pages", "") + ("-" if tag == "EP" and cur.get("pages") else "") + val).strip()
        elif tag == "LA":
            cur["language"] = val
    out = []
    for r in records:
        if not r.get("title"):
            continue
        r.setdefault("year", None)
        r.setdefault("journal", "")
        r.setdefault("doi", "")
        out.append(r)
    return out


# ---------- 入库 ----------

def _aff_token(aff: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (aff or "").lower())[:12]


def resolve_webvpn_author(conn, rec_author: dict, domain_id: str) -> str:
    """汉字名按 name_zh/name_display 精确匹配（机构短键防误并）；拼音名按 pinyin_norm 匹配。
    均未命中则新建（外围层，别名待校对）。返回 BG ID。"""
    name = rec_author["raw_name"]
    aff = _aff_token(rec_author.get("affiliation") or "")
    row = None
    if HANZI.search(name):
        cands = conn.execute(
            """SELECT id, name_zh, name_display FROM authors
               WHERE name_zh=? OR name_display=? ORDER BY
                 (SELECT COUNT(*) FROM paper_authors pa WHERE pa.author_id=authors.id) DESC""",
            (name, name)).fetchall()
        for c in cands:
            if aff:
                note = conn.execute("SELECT note FROM authors WHERE id=?", (c["id"],)).fetchone()
                if note and note["note"] and f"fbkey:{aff}" in (note["note"] or ""):
                    return c["id"]
            row = c
            break
        if row:
            return row["id"]
    else:
        key = norm_pinyin(name)
        if key:
            row = conn.execute("SELECT id FROM authors WHERE pinyin_norm=? ORDER BY "
                               "(SELECT COUNT(*) FROM paper_authors pa WHERE pa.author_id=authors.id) DESC",
                               (key,)).fetchone()
            if row:
                return row["id"]
    aid = next_author_id(conn)
    conn.execute(
        """INSERT INTO authors(id, name_display, name_sort, name_zh, pinyin_norm, tier, note)
           VALUES(?,?,?,?,?, 'peripheral', ?)""",
        (aid, name, name_sort(name), name if HANZI.search(name) else None,
         norm_pinyin(name), f"webvpn fbkey:{aff or 'none'} low_confidence"))
    alias_type = "hanzi" if HANZI.search(name) else "pinyin"
    conn.execute(
        "INSERT OR IGNORE INTO author_aliases(author_id, alias, alias_type, source, confidence, verified) "
        "VALUES(?,?,?, 'webvpn', 0.5, 0)", (aid, name, alias_type))
    audit(conn, "system", "author.create_webvpn", "author", aid,
          {"name": name, "source": "webvpn", "domain": domain_id})
    return aid


def import_records(conn, domain_id: str, records: list[dict], source: str, query: str = ""):
    """统一记录入库：论文 upsert + 作者解析 + 收录留痕。返回 (n_new, n_dup, n_authors)。"""
    n_new = n_dup = n_authors = 0
    for rec in records:
        t_norm = norm_title(rec.get("title", ""))
        if not t_norm:
            n_dup += 1
            continue
        fa = rec["authors"][0]["pinyin_norm"] if rec.get("authors") else ""
        row = None
        if rec.get("doi"):
            row = conn.execute("SELECT id FROM papers WHERE doi=?", (rec["doi"],)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM papers WHERE title_norm=? AND first_author_norm=? AND year=?",
                (t_norm, fa, rec.get("year"))).fetchone()
        if row:
            pid = row["id"]
            n_dup += 1
        else:
            cur = conn.execute(
                """INSERT INTO papers(doi, title, title_norm, year, journal, volume, pages,
                   abstract, pub_types, mesh, language, retraction_status, first_author_norm)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rec.get("doi") or None, rec.get("title", ""), t_norm, rec.get("year"),
                 rec.get("journal", ""), rec.get("volume", ""), rec.get("pages", ""),
                 rec.get("abstract", ""),
                 json.dumps(rec.get("pub_types", []), ensure_ascii=False),
                 json.dumps(rec.get("mesh", []), ensure_ascii=False),
                 rec.get("language", "") or None, rec.get("retraction_status", "none"), fa))
            pid = cur.lastrowid
            n_new += 1
        # 收录留痕：webvpn 导入本质是用户在源站执行的关键词检索
        conn.execute(
            "INSERT OR IGNORE INTO paper_domains(paper_id, domain_id, rule, matched_term) VALUES(?,?, 'keyword', ?)",
            (pid, domain_id, f"webvpn:{source}:{query or 'manual_search'}"))
        # 作者解析
        for a in rec.get("authors", []):
            aid = resolve_webvpn_author(conn, a, domain_id)
            conn.execute(
                "INSERT OR IGNORE INTO paper_authors(paper_id, author_id, position, raw_name) VALUES(?,?,?,?)",
                (pid, aid, a["position"], a["raw_name"]))
            n_authors += 1
        # 机构履历
        for a in rec.get("authors", []):
            aff = (a.get("affiliation") or rec.get("affiliations") or "").strip()
            if not aff:
                continue
            aid = conn.execute("SELECT author_id FROM paper_authors WHERE paper_id=? AND position=?",
                               (pid, a["position"])).fetchone()
            if aid:
                for inst in [x.strip() for x in re.split(r"[;；|]", aff) if x.strip()][:3]:
                    conn.execute(
                        """INSERT OR IGNORE INTO affiliations(author_id, institution, institution_norm,
                           start_year, end_year, source_tag, confidence)
                           VALUES(?,?,?,?,?, 'auto', 0.7)""",
                        (aid["author_id"], inst, norm_title(inst), rec.get("year"), rec.get("year")))
    return n_new, n_dup, n_authors


def ingest_file(path: Path, source: str, domain_id: str, query: str = "", dry_run=False):
    text = path.read_text(encoding="utf-8", errors="replace")
    fmt = "csv" if source == "scopus" else "ris"
    records = parse_scopus_csv(text) if fmt == "csv" else parse_ris(text)
    if not records:
        raise SystemExit(f"[webvpn] 未解析到任何题录（{source}），请检查导出格式")
    print(f"[webvpn] {source}: 解析 {len(records)} 条题录（{path.name}）")
    conn = connect()
    init_db(conn)
    ensure_domain(conn, domain_id)
    # 文件指纹去重
    h = file_hash(path)
    dup = conn.execute("SELECT id, n_new, imported_at FROM webvpn_imports WHERE file_hash=?", (h,)).fetchone()
    if dup:
        print(f"⚠ 该文件已导入过（批次 #{dup['id']}，{dup['imported_at']}，新增 {dup['n_new']} 篇）。如确需重导请改文件名。")
        return
    if dry_run:
        print("[dry-run] 仅解析预览：")
        for r in records[:3]:
            print(f"  - {r.get('year')} | {r.get('title', '')[:60]} | {r.get('journal', '')[:30]} | 作者 {len(r.get('authors', []))}")
        return
    n_new, n_dup, n_authors = import_records(conn, domain_id, records, source, query)
    conn.execute(
        """INSERT INTO webvpn_imports(domain_id, source, file_name, file_hash, query, n_records, n_new, n_dup)
           VALUES(?,?,?,?,?,?,?,?)""",
        (domain_id, source, path.name, h, query or None, len(records), n_new, n_dup))
    audit(conn, "system", "webvpn.import", "domain", domain_id,
          {"source": source, "file": path.name, "records": len(records),
           "new": n_new, "dup": n_dup, "authors": n_authors})
    conn.commit()
    print(f"[webvpn] 完成：新增论文 {n_new}，去重 {n_dup}，关联作者 {n_authors}")
    print(f"         下一步：python3 analyze/graph.py {domain_id}  刷新图谱")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="机构 webvpn 题录导入（Scopus CSV / CNKI / 万方 RIS）")
    ap.add_argument("file")
    ap.add_argument("--source", required=True, choices=["scopus", "cnki", "wanfang"],
                    help="scopus=Scopus CSV；cnki/wanfang=中文 RIS 题录")
    ap.add_argument("--domain", required=True, help="目标域 ID")
    ap.add_argument("--query", default="", help="源站检索式/来源说明（留痕用）")
    ap.add_argument("--dry-run", action="store_true", help="仅解析预览")
    args = ap.parse_args()
    ingest_file(Path(args.file), args.source, args.domain, args.query, args.dry_run)
