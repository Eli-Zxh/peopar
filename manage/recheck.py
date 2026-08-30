"""收录复核：对域内按 author/keyword 规则收录的论文做确定性再验证。

背景：PubMed 检索式在服务端可能被自动改写（未加引号的多词短语 + [tiab] 限定
会被拆词并做 ATM 扩展，导致误命中）。本工具在本地以库内数据为准复核：

  author 规则   → 论文作者署名须匹配配置中的 author_queries
  keyword 规则  → 标题/摘要须包含关键词字面（通配符转为正则）
  cite_seed 规则 → 引用扩散是显式规则，信任（留痕可查）

用法：
  python3 manage/recheck.py <domain> [--dry-run]

不匹配的论文将从该域清退（删除 paper_domains 链接；论文本身保留，可能属于
其他域）。其证据链上若挂着方向快照，相应快照置为 affected_pending_review。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, load_domain_config, init_db


def parse_author_query(q: str):
    """'Lesne S[Author]' → ('lesne', 's')；只处理 姓+首字母 形式。"""
    m = re.match(r"^(.+?)\s*\[Author\]$", q, re.I)
    if not m:
        return None
    parts = m.group(1).strip().split()
    if len(parts) < 2:
        return (parts[0].lower(), None)
    return (parts[0].lower(), parts[-1][0].lower())


def norm_sig(raw: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", raw.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", "", s.strip())


def keyword_regex(k: str):
    """'"Abeta 56"[tiab]' → 匹配标题/摘要中该词面（容忍连写、连字符、β 写法）的正则。"""
    word = re.sub(r"\[[^\]]*\]$", "", k).strip().strip('"').lower()
    word = word.replace("β", "beta")
    pat, i = "", 0
    while i < len(word):
        if word[i] == "*":
            pat += ".*"
            i += 1
        elif word[i:i + 4] == "beta":
            pat += r"(?:beta|β)"
            i += 4
        elif word[i] == " ":
            pat += r"[\s\-]?"
            i += 1
        else:
            pat += re.escape(word[i])
            i += 1
    return re.compile(pat, re.I)


def main():
    ap = argparse.ArgumentParser(description="收录复核")
    ap.add_argument("domain")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_domain_config(args.domain)
    conn = connect()
    init_db(conn)
    p = cfg["pubmed"]
    author_pats = [parse_author_query(q) for q in p.get("author_queries", [])]
    author_pats = [a for a in author_pats if a]
    kw_res = [keyword_regex(k) for k in p.get("keywords_strong", [])]

    rows = conn.execute(
        """SELECT pd.paper_id, pd.rule, pd.matched_term, p.title, p.abstract
           FROM paper_domains pd JOIN papers p ON p.id=pd.paper_id
           WHERE pd.domain_id=? AND pd.rule IN ('author','keyword')""", (args.domain,)).fetchall()

    keep, drop = [], []
    for r in rows:
        ok = False
        sigs = [norm_sig(x["raw_name"]) for x in conn.execute(
            "SELECT raw_name FROM paper_authors WHERE paper_id=?", (r["paper_id"],))]
        if not sigs:
            sigs = [norm_sig(x["raw_name"]) for x in conn.execute(
                "SELECT raw_name FROM paper_author_staging WHERE paper_id=?", (r["paper_id"],))]
        if author_pats:
            for last, init in author_pats:
                for s in sigs:
                    toks = s.split()
                    if toks and toks[0] == last and (
                            init is None or any(t.startswith(init) for t in toks[1:])):
                        ok = True
                        break
        if not ok and kw_res:
            text = (r["title"] or "") + " " + (r["abstract"] or "")
            ok = any(rx.search(text) for rx in kw_res)
        (keep if ok else drop).append(r)

    print(f"[recheck] 复核 {len(rows)} 篇（author/keyword 规则）：保留 {len(keep)}，清退 {len(drop)}")
    for r in drop:
        print(f"  清退 paper {r['paper_id']}（{r['rule']}）: {(r['title'] or '')[:70]}")
    if args.dry_run:
        print("[dry-run] 未执行")
        return
    if not drop:
        return

    drop_ids = [r["paper_id"] for r in drop]
    ph = ",".join("?" * len(drop_ids))
    # 证据链影响：挂了这些论文的快照 → 待复核
    snaps = conn.execute(
        f"SELECT DISTINCT snapshot_id FROM evidence WHERE paper_id IN ({ph})", drop_ids).fetchall()
    for s in snaps:
        conn.execute("UPDATE snapshots SET status='affected_pending_review' WHERE id=?", (s["snapshot_id"],))
        audit(conn, "system", "snapshot.affected", "snapshot", s["snapshot_id"],
              {"reason": "evidence_paper_dropped", "domain": args.domain})
    conn.execute(f"DELETE FROM paper_domains WHERE domain_id=? AND paper_id IN ({ph})",
                 [args.domain] + drop_ids)
    audit(conn, "system", "recheck.drop", "domain", args.domain,
          {"dropped": drop_ids, "kept": len(keep), "total": len(rows)})
    conn.commit()
    print(f"[recheck] 已清退 {len(drop_ids)} 篇域链接；受影响快照 {len(snaps)} 条")
    print("下一步：python3 analyze/graph.py %s  # 重建簇与图谱" % args.domain)


if __name__ == "__main__":
    main()
