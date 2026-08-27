// P2 方向级快照合成：并行 fan-out，每个主要簇由一个子代理按 cluster_snapshot.md 模板合成
// 依赖：data/pack_<domain>.json（analyze/extract.py 产物）；产物需 manage/snapshot.py apply 入库
// 用法：workflow args = { domain, pack_path, clusters: [ {cluster_id, label} ... ] }
const packPath = args.pack_path;
const domain = args.domain;

phase("方向级快照合成（并行）");
const results = [];
const items = args.clusters.map(c => c);

const out = await parallel(items.map(c => () =>
  agent(`你是文献计量分析助手。请完成一个研究方向的快照合成任务。

任务：读取本地文件 ${packPath}（JSON），找到 cluster_id=${c.cluster_id} 的簇对象；再读取 /Users/zhangxinhao/Documents/script/peopar/prompts/cluster_snapshot.md（合成模板 v2）。严格按模板的「输出（严格 JSON）」与「硬约束」合成该簇的方向快照。

输出：单个 JSON 对象，包含 cluster_id、name、definition、current_conclusions、timeline、controversies、representative_paper_ids、supporting_paper_ids 字段（paper_id 必须来自该簇 papers 数组，且代表/支撑论文不得重复）。只输出 JSON，不要其他文字。`, { label: `cluster-${c.cluster_id}`, phase: "方向级快照合成（并行）" })
));

const snapshots = out.filter(Boolean).map((s, i) => {
  let obj = null;
  try { obj = typeof s === "string" ? JSON.parse(s) : s; } catch { obj = null; }
  return obj;
}).filter(Boolean);

return {
  domain,
  prompt_ver: "cluster_snapshot.md@v2-20260826",
  model: "peopar-agent@2026-08-27 (workflow fan-out)",
  snapshots,
  requested: args.clusters.length,
  produced: snapshots.length,
  save_to: `data/${domain}_snap_batch.json`,
};
