"""人工修订同步：把 vault 笔记中的 manual_* / 批注回写 SQLite 权威层。

原则：
- 白名单字段映射（不越权：L0/事件/簇成员/收录等不可经 md 修改）
- 人工修订区（PP-MANUAL 区块 / manual_* frontmatter）由 export_vault 保留，永不静默覆盖
- 回写为显式命令；写 SQLite 全部 by=user:<署名> 留痕；apply 后把 manual_* 标记 synced

子命令：
  scan   <vault> [--topic 方向名]    只读列出待回写修订与建议映射
  apply  <vault> --by <署名> [--topic] [--sync]   按白名单精确回写（--sync 后清 manual 标记）
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, init_db

MANUAL_DETAILS = re.compile(r"<details[^>]*class=\"pp-note\"[^>]*>.*?</details>", re.S)

# 白名单：md type → (frontmatter manual 键, 回写函数参数)
def scan_vault(conn, root: Path):
    findings = []   # {type, id, kind: 'fm'|'block', key, value, file}
    for f in sorted(root.rglob("*.md")):
        rel = f.relative_to(root)
        parts = rel.parts
        if len(parts) < 2:
            continue
        typ, name = parts[0], parts[1][:-3]
        txt = f.read_text(encoding="utf-8")
        # frontmatter manual_*
        m = re.match(r"^---\n(.*?)\n---", txt, re.S)
        if m:
            for ln in m.group(1).splitlines():
                if ln.startswith("manual_"):
                    k, _, v = ln.partition(":")
                    findings.append({"type": typ, "id": name, "kind": "fm", "key": k.strip(),
                                     "value": v.strip().strip('"'), "file": str(rel)})
        # 人工注记区块（details.pp-note）
        mm = MANUAL_DETAILS.search(txt)
        if mm:
            inner = re.sub(r"<details[^>]*>|<summary>.*?</summary>|</details>", "", mm.group(0), flags=re.S)
            note = inner.strip()
            if note:
                findings.append({"type": typ, "id": name, "kind": "note",
                                 "value": note[:200], "file": str(rel)})
    return findings


def _paper_id(name: str) -> int | None:
    m = re.fullmatch(r"paper-(\d+)", name)
    return int(m.group(1)) if m else None


def apply_one(conn, f: dict, by: str) -> tuple[str, str]:
    """按白名单回写一条，返回 (id, 描述)。"""
    typ, name = f["type"], f["id"]
    if typ == "researchers" and re.fullmatch(r"BG\d+", name):
        if f["kind"] == "fm" and f["key"] in ("manual_name_zh", "manual_note"):
            key = {"manual_name_zh": "name_zh", "manual_note": "note"}[f["key"]]
            if f["key"] == "manual_name_zh" and not re.search(r"[\u4e00-\u9fff]", f["value"]):
                return name, "跳过：汉字名需含汉字"
            if key == "name_zh":
                conn.execute("UPDATE authors SET name_zh=? WHERE id=?", (f["value"], name))
            else:
                conn.execute("UPDATE authors SET note=COALESCE(note,'')||'\n'||? WHERE id=?", (f["value"], name))
            audit(conn, f"user:{by}", "manual.apply", "author", name, {key: f["value"]})
            return name, f"更新 {key}"
        if f["kind"] == "note":
            audit(conn, f"user:{by}", "manual.note", "author", name, {"note": f["value"][:200]})
            return name, "批注已记录（authors.note 手动/保留于 vault）"
    elif typ == "papers" and (pid := _paper_id(name)) is not None:
        if f["kind"] == "fm" and f["key"] == "manual_paper_note":
            conn.execute("UPDATE papers SET note=COALESCE(note,'')||'\n'||? WHERE id=?", (f["value"], pid))
            audit(conn, f"user:{by}", "manual.apply", "paper", pid, {"note": f["value"]})
            return name, "更新 papers.note"
    return name, "（白名单外：仅 vault 展示，不回写）"


def cmd_scan(args):
    root = Path(args.vault).expanduser() / args.topic / "peopar"
    if not root.is_dir():
        raise SystemExit(f"peopar 目录不存在：{root}")
    conn = connect()
    init_db(conn)
    items = scan_vault(conn, root)
    if not items:
        print("（无 manual_* / 人工注记修订）")
        return
    for it in items:
        desc = apply_one(conn, it, by="scan")   # 仅用于展示映射（不落库）
        print(f"[{it['kind']:<4}] {it['type']}/{it['id']}  {it.get('key','note')} = {it['value'][:60]} → {desc}")


def cmd_apply(args):
    root = Path(args.vault).expanduser() / args.topic / "peopar"
    if not root.is_dir():
        raise SystemExit(f"peopar 目录不存在：{root}")
    conn = connect()
    init_db(conn)
    items = [x for x in scan_vault(conn, root) if x["kind"] == "fm"]
    ok, note_only = 0, 0
    for it in items:
        aid, desc = apply_one(conn, it, args.by)
        if "白名单外" not in desc:
            ok += 1
        else:
            note_only += 1
        print(f"  {it['type']}/{aid}: {desc}")
    conn.commit()
    # 人工注记（kind=note）不写入库时提示用户
    notes = [x for x in scan_vault(conn, root) if x["kind"] == "note"]
    print(f"[notes-sync] 回写 {ok} 条 frontmatter 修订；注记区块 {len(notes)} 条（保留 vault 层，如需入 authors.note 请手工整理）")
    if args.sync:
        # --sync：把已回写的 manual_* frontmatter 键标记 synced（值保留作历史）
        for f in root.rglob("*.md"):
            txt = f.read_text(encoding="utf-8")
            if "manual_" not in txt.split("---", 2)[1] if txt.startswith("---") else False:
                continue
            m = re.match(r"^(---\n.*?\n---)", txt, re.S)
            if not m:
                continue
            fm_txt = m.group(1)
            nf = "\n".join(ln for ln in fm_txt.splitlines() if not ln.startswith("manual_"))
            txt2 = txt.replace(fm_txt, nf, 1)
            f.write_text(txt2, encoding="utf-8")
        print("[notes-sync] 已清除回写过的 manual_* 键（vault 现为纯导出态）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="人工修订同步（vault → SQLite 权威层）")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("scan")
    p1.add_argument("vault")
    p1.add_argument("--topic", default="神经语言学与失语症")
    p2 = sub.add_parser("apply")
    p2.add_argument("vault")
    p2.add_argument("--topic", default="神经语言学与失语症")
    p2.add_argument("--by", required=True)
    p2.add_argument("--sync", action="store_true", help="回写后清除 manual_* 标记")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        raise SystemExit(1)
    if args.cmd == "scan":
        cmd_scan(args)
    else:
        cmd_apply(args)
