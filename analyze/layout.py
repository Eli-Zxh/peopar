"""方向图谱布局求解器（确定性，实现 doc/visualization-model.md v0.1）。

输入：SQLite 中 approved 的布局评分（--include-pending 预演可用 pending）+ 方向/论文/作者统计。
求解：
  1) 方向中心：热度加权角度基线（扇区角 ∝ 热度^α，圆心同 R_base 圆周）→
     按 dir_sim 关联度做弹簧松弛（s 高则拉近）→ 冲突解决（低 s 过近则推开）
  2) 论文位置：主方向径向 R_i·(1-a(j,i))；未评分论文按 seed 随机排布（存在性展示）
  3) 研究者位置：其（空间化）论文坐标加权质心；无论文者方向环带 seed 随机
  4) 半径：论文 r = r_min+(r_max-r_min)·u^γ，u=α·cite_rank_pct+β·core_frac（受约束映射）
输出：写 node_layout 表（batch_id 可复现）+ data/layout_<domain>.json

用法：python3 analyze/layout.py <domain> [--k 12] [--include-pending] [--seed 42] [--out json]
"""
import argparse
import json
import math
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingest.common import audit, connect, init_db

# ---------- 半径映射（受约束物理量） ----------
R_MIN, R_MAX = 5.0, 34.0   # 论文显示半径上下界（相对画布缩放前）
GAMMA = 0.85               # 半径分布形状
ALPHA, BETA = 0.7, 0.3     # 被引百分位 vs 簇核心占比 权重
R_BASE = 320.0             # 方向中心圆周半径（画布半宽基准）
R_DIR_MIN, R_DIR_MAX = 70.0, 190.0  # 方向区域半径上下界
MIN_GAP = 34.0             # 方向间最小中心距余量
HOT_ALPHA = 0.6            # 扇区角热度指数
K_STRONG = 0.5             # 弹簧松弛增益
N_ITER = 60                # 松弛迭代次数
N_PAPER_DISPLAY = 40       # 每方向显示论文上限（评分+随机补充）
N_AUTHOR_DISPLAY = 12      # 每方向显示作者上限
SECONDARY_TH = 0.6         # 次方向偏移阈值


def latest_clusters(conn, domain, limit=None):
    q = """SELECT c.* FROM clusters c
           JOIN (SELECT cluster_id FROM author_clusters GROUP BY cluster_id HAVING COUNT(*)>=3) s
             ON s.cluster_id=c.id
           WHERE c.domain_id=? AND c.batch_id=
             (SELECT MAX(batch_id) FROM clusters WHERE domain_id=?)
           ORDER BY (SELECT COUNT(*) FROM author_clusters ac WHERE ac.cluster_id=c.id) DESC"""
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, (domain, domain)).fetchall()


def cluster_stats(conn, cid):
    size = conn.execute("SELECT COUNT(*) n FROM author_clusters WHERE cluster_id=?", (cid,)).fetchone()["n"]
    row = conn.execute(
        """SELECT COUNT(DISTINCT paper_id) np, SUM(c) cit FROM (
               SELECT DISTINCT pa.paper_id AS paper_id, p.cited_by_count c FROM author_clusters ac
               JOIN paper_authors pa ON pa.author_id=ac.author_id
               JOIN papers p ON p.id=pa.paper_id WHERE ac.cluster_id=?)""", (cid,)).fetchone()
    return size, row["np"] or 0, row["cit"] or 0


def direction_layout(conn, domain, clusters, sim):
    """方向中心：热度加权角度基线 + 弹簧松弛 + 冲突解决。返回 {cid: (x,y,R)}。"""
    hot = {}
    for c in clusters:
        size, np_, cit = cluster_stats(conn, c["id"])
        hot[c["id"]] = (math.log1p(size) * math.log1p(np_ + 1)) ** HOT_ALPHA
    tot = sum(hot.values())
    # 扇区角 ∝ 热度（首方向从 -90° 起始，相位固定 0.3）
    out, acc = {}, 0.0
    for c in sorted(clusters, key=lambda x: -hot[x["id"]]):
        frac = hot[c["id"]] / tot
        theta = acc + frac / 2  # 扇区中位角
        acc += frac
        out[c["id"]] = [R_BASE * math.cos(2 * math.pi * theta + 0.3),
                        R_BASE * math.sin(2 * math.pi * theta + 0.3)]
    # 区域半径：受簇规模与画布约束
    sizes = {c["id"]: cluster_stats(conn, c["id"])[0] for c in clusters}
    max_size = max(sizes.values()) or 1
    for c in clusters:
        frac = (sizes[c["id"]] / max_size) ** 0.5
        out[c["id"]].append(R_DIR_MIN + (R_DIR_MAX - R_DIR_MIN) * frac)
    # 弹簧松弛：s 高拉近
    for _ in range(N_ITER):
        moved = 0.0
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                a, b = clusters[i]["id"], clusters[j]["id"]
                s = sim.get((min(a, b), max(a, b)), 0.0)
                if s <= 0.05:
                    continue
                dx = out[b][0] - out[a][0]
                dy = out[b][1] - out[a][1]
                dist = math.hypot(dx, dy) + 1e-6
                pull = K_STRONG * s * (dist - (out[a][2] + out[b][2]) * 0.7) / max(dist, 1.0)
                ox, oy = pull * dx / dist, pull * dy / dist
                out[a][0] += ox; out[a][1] += oy
                out[b][0] -= ox; out[b][1] -= oy
                moved += abs(pull)
        # 冲突解决：低 s 且过近 → 推开
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                a, b = clusters[i]["id"], clusters[j]["id"]
                s = sim.get((min(a, b), max(a, b)), 0.0)
                dx = out[b][0] - out[a][0]; dy = out[b][1] - out[a][1]
                dist = math.hypot(dx, dy) + 1e-6
                need = out[a][2] + out[b][2] + MIN_GAP * (1 + 0.8 * (1 - s))
                if dist < need and s < 0.7:
                    push = (need - dist) / 2
                    ux, uy = dx / dist, dy / dist
                    out[a][0] -= ux * push; out[a][1] -= uy * push
                    out[b][0] += ux * push; out[b][1] += uy * push
        if moved < 0.5:
            break
    return out


def paper_radius(u):  # u ∈ [0,1] → [R_MIN, R_MAX]
    return R_MIN + (R_MAX - R_MIN) * (max(0.0, min(1.0, u)) ** GAMMA)


def solve(domain, k=12, include_pending=False, seed=42, out_json=None):
    conn = connect()
    init_db(conn)
    clusters = latest_clusters(conn, domain, limit=k)
    cids = [c["id"] for c in clusters]
    if not cids:
        raise SystemExit("无方向")
    status = "'approved','pending'" if include_pending else "'approved'"
    # 评分
    sim = {}
    for r in conn.execute(
            f"SELECT from_id, to_id, value FROM layout_scores WHERE domain_id=? AND score_type='dir_sim' "
            f"AND status IN ({status})", (domain,)):
        a, b = int(r["from_id"]), int(r["to_id"])
        if a in cids and b in cids:
            sim[(min(a, b), max(a, b))] = r["value"]
    aff = defaultdict(list)  # cid -> [(paper_id, a)]
    for r in conn.execute(
            f"SELECT from_id, to_id, value FROM layout_scores WHERE domain_id=? AND score_type='paper_aff' "
            f"AND status IN ({status})", (domain,)):
        aff[int(r["to_id"])].append((int(r["from_id"]), r["value"]))

    rng = random.Random(seed)
    centers = direction_layout(conn, domain, clusters, sim)

    # 论文半径映射参数（被引百分位 + 核心占比）
    domain_papers = {r["paper_id"] for r in conn.execute(
        "SELECT paper_id FROM paper_domains WHERE domain_id=?", (domain,))}
    cite_of = {}
    for pid in domain_papers:
        cite_of[pid] = conn.execute("SELECT cited_by_count FROM papers WHERE id=?", (pid,)).fetchone()[0] or 0

    nodes_dir, nodes_paper, nodes_author, edges = [], [], [], []
    placed_global = set()   # 已被某方向主定位的论文（全局唯一，跨方向论文只落一次）
    # --- 方向层 ---
    for c in clusters:
        cid = c["id"]
        size, np_, cit = cluster_stats(conn, cid)
        x, y, R = centers[cid]
        nodes_dir.append({"id": f"dir:{cid}", "cluster_id": cid, "name": c["name"], "x": x, "y": y,
                          "r": R, "size": size, "papers": np_})
        # --- 论文层：评分论文径向定位 ---
        ranked = sorted(aff.get(cid, []), key=lambda t: -cite_of.get(t[0], 0))
        tot_p = max(len(ranked), 1)
        pos = 0
        placed = []
        for pid, a in ranked:
            if pid not in domain_papers or pid in placed_global:
                continue
            cite = cite_of.get(pid, 0)
            u = ALPHA * (pos / max(tot_p - 1, 1)) + BETA * 0.5  # cite 百分位近似（排序位）+ 核心占比近似
            r = paper_radius(u)
            d = R * (1 - a)
            ang = 2 * math.pi * (pos * 0.6180339887 + rng.random() * 0.2)  # 黄金角
            px, py = x + d * math.cos(ang), y + d * math.sin(ang)
            nodes_paper.append({"id": f"p:{pid}", "cluster_id": cid, "paper_id": pid, "x": px, "y": py,
                                "r": r, "affinity": a, "cite": cite, "main_dir": cid, "sec": []})
            placed.append(pid)
            placed_global.add(pid)
            pos += 1
        # 随机补充至 N_PAPER_DISPLAY（存在性，仅该簇内论文，且全局未放置）
        cluster_pids = [r["paper_id"] for r in conn.execute(
            """SELECT DISTINCT pa.paper_id FROM author_clusters ac
               JOIN paper_authors pa ON pa.author_id=ac.author_id
               JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
               WHERE ac.cluster_id=?""", (domain, cid))]
        more = [pid for pid in cluster_pids if pid not in placed and pid not in placed_global]
        rng.shuffle(more)
        for pid in more:
            if len([n for n in nodes_paper if n.get("cluster_id") == cid]) >= N_PAPER_DISPLAY:
                break
            ang = rng.random() * 2 * math.pi
            rr = R * (0.15 + 0.8 * rng.random())
            nodes_paper.append({"id": f"p:{pid}", "cluster_id": cid, "paper_id": pid,
                                "x": x + rr * math.cos(ang), "y": y + rr * math.sin(ang),
                                "r": paper_radius(rng.random()), "affinity": None, "cite": cite_of.get(pid, 0),
                                "main_dir": cid, "sec": []})
            placed_global.add(pid)
        # --- 作者层：方向 top 作者 ---
        authors = conn.execute(
            """SELECT a.id, a.name_display, a.name_zh,
                      COUNT(DISTINCT pa.paper_id) np FROM author_clusters ac
               JOIN authors a ON a.id=ac.author_id
               LEFT JOIN paper_authors pa ON pa.author_id=a.id
               LEFT JOIN paper_domains pd ON pd.paper_id=pa.paper_id AND pd.domain_id=?
               WHERE ac.cluster_id=? GROUP BY a.id ORDER BY np DESC LIMIT ?""",
            (domain, cid, N_AUTHOR_DISPLAY)).fetchall()
        # 该方向论文中作者的论文（近似的论文质心：取该方向内被空间化且作者参与的论文）
        for a in authors:
            aid = a["id"]
            mine = [n for n in nodes_paper
                    if n.get("main_dir") == cid and _paper_has_author(conn, aid, n["paper_id"])]
            if mine:
                ax = sum(n["x"] for n in mine) / len(mine)
                ay = sum(n["y"] for n in mine) / len(mine)
            else:
                ang = rng.random() * 2 * math.pi
                rr = R * (0.6 + 0.35 * rng.random())
                ax, ay = x + rr * math.cos(ang), y + rr * math.sin(ang)
            nodes_author.append({"id": aid, "name": a["name_display"], "zh": a["name_zh"],
                                 "x": ax, "y": ay, "r": 8 + 2 * math.log1p(a["np"] or 0), "cluster_id": cid})

    edges = _build_edges(conn, nodes_author, nodes_paper)
    edges.extend(_cross_dir_edges(conn, aff, nodes_paper, centers))

    for pp in nodes_paper:
        rr = conn.execute("SELECT abstract, note, pmid FROM papers WHERE id=?", (pp["paper_id"],)).fetchone()
        if rr:
            pp["abstract"] = (rr["abstract"] or "")[:160]
            pp["note"] = rr["note"] or ""
            pp["pmid"] = rr["pmid"] or None

    batch = f"layout-{date.today().isoformat()}-{seed}"
    conn.execute("DELETE FROM node_layout WHERE domain_id=? AND batch_id=?", (domain, batch))
    for t, arr in (("direction", nodes_dir), ("paper", nodes_paper), ("author", nodes_author)):
        for n in arr:
            conn.execute(
                "INSERT INTO node_layout(domain_id,batch_id,type,id,x,y,r,cluster_id,main_dir,affinity) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (domain, batch, t, n["id"], n["x"], n["y"], n["r"],
                 n.get("cluster_id"), n.get("main_dir"), n.get("affinity")))
    audit(conn, "system", "layout.solve", "domain", domain,
          {"batch": batch, "dirs": len(nodes_dir), "papers": len(nodes_paper), "authors": len(nodes_author)})
    conn.commit()
    data = {"domain": domain, "batch": batch, "seed": seed,
            "directions": nodes_dir, "papers": nodes_paper, "authors": nodes_author, "edges": edges}
    if out_json:
        p = Path(out_json)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"[layout] 已写 {p}")
    print(f"[layout] {domain} batch={batch}: 方向 {len(nodes_dir)} / 论文 {len(nodes_paper)} / 作者 {len(nodes_author)}")
    return data


def _shared_authors(conn, pid1, pid2) -> int:
    r = conn.execute(
        "SELECT COUNT(*) n FROM (SELECT author_id FROM paper_authors WHERE paper_id=? "
        "INTERSECT SELECT author_id FROM paper_authors WHERE paper_id=?)",
        (pid1, pid2)).fetchone()
    return r["n"] if r else 0


def _papers_of_author(conn, aid, by_pid):
    ids = [r["paper_id"] for r in conn.execute(
        "SELECT paper_id FROM paper_authors WHERE author_id=?", (aid,))]
    return [pp for pid in ids for pp in by_pid.get(pid, [])]


def _build_edges(conn, authors, papers):
    """三种连线：authored 作者-论文 / cowrite 同方向论文-论文共享作者（每论文邻接 ≤3）。"""
    edges = []
    by_pid = {}
    for pp in papers:
        by_pid.setdefault(pp["paper_id"], []).append(pp)
    for a in authors:
        mine = _papers_of_author(conn, a["id"], by_pid)
        mine.sort(key=lambda x: -(x.get("cite") or 0))
        for pp in mine[:3]:
            edges.append({"source": a["id"], "target": pp["id"], "kind": "authored", "w": 1})
    by_dir = {}
    for pp in papers:
        by_dir.setdefault(pp.get("main_dir"), []).append(pp)
    degree = {}
    for cid, ps in by_dir.items():
        if len(ps) > 45:
            continue
        pairs = []
        for i in range(len(ps)):
            for k in range(i + 1, len(ps)):
                u, v = ps[i], ps[k]
                if u.get("cluster_id") != v.get("cluster_id"):
                    continue
                w = _shared_authors(conn, u["paper_id"], v["paper_id"])
                if w:
                    pairs.append((w, u["id"], v["id"]))
        for w, u, v in sorted(pairs, key=lambda x: -x[0]):
            if degree.get(u, 0) >= 3 or degree.get(v, 0) >= 3:
                continue
            edges.append({"source": u, "target": v, "kind": "cowrite", "w": w})
            degree[u] = degree.get(u, 0) + 1
            degree[v] = degree.get(v, 0) + 1
    return edges


def _cross_dir_edges(conn, aff, papers, centers):
    """跨方向论文 → 次方向中心虚线：论文对其它方向 affinity ≥0.55 且非主方向。"""
    out = []
    by_paper = {}
    for cid, lst in aff.items():
        for pid, a in lst:
            by_paper.setdefault(pid, []).append((cid, a))
    for pp in papers:
        for cid, a in by_paper.get(pp["paper_id"], []):
            if cid == pp.get("main_dir"):
                continue
            if a >= 0.55:
                cx, cy, _ = centers.get(cid, (0, 0, 0))
                out.append({"source": pp["id"], "target_x": cx, "target_y": cy,
                            "to_dir": cid, "kind": "crossdir", "w": a})
    return out



def _paper_has_author(conn, aid, pid):
    return conn.execute("SELECT 1 FROM paper_authors WHERE author_id=? AND paper_id=? LIMIT 1",
                        (aid, pid)).fetchone() is not None


def _attach_author_edges(conn, authors, papers, edges):
    """作者 ↔ 其被放置论文连线（每人至多 3 条最近被引）。"""
    for a in authors:
        mine = [p for p in papers if _paper_has_author(conn, a["id"], p["paper_id"])]
        mine.sort(key=lambda p: -(p.get("cite") or 0))
        for p in mine[:3]:
            edges.append({"source": a["id"], "target": p["id"], "kind": "authored"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="方向图谱布局求解")
    ap.add_argument("domain")
    ap.add_argument("--k", type=int, default=12)
    ap.add_argument("--include-pending", action="store_true", help="预演：pending 评分也参与（正式流程用 approved）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", help="额外写 JSON 到该路径")
    args = ap.parse_args()
    solve(args.domain, args.k, args.include_pending, args.seed, args.out)
