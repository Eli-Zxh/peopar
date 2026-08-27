// P2b 作者级画像合成：并行 fan-out，每个上下文包由一个子代理按 author_profile.md 模板合成
// 依赖：data/pack_authors_<domain>_<n>.json（analyze/extract.py --authors 产物）
// 产物需 manage/snapshot.py apply-authors 入库
// 用法：workflow args = { domain, packs: ["data/pack_authors_neuroling_1.json", ...] }
const domain = args.domain;
phase("作者级画像合成（并行）");
const out = await parallel(args.packs.map(p => () =>
  agent(`你是文献计量分析助手。请完成一批研究者画像合成任务。

任务：读取本地文件 /Users/zhangxinhao/Documents/script/peopar/${p}（JSON，含 authors 数组，每元素一位研究者的确定性记录）；再读取 /Users/zhangxinhao/Documents/script/peopar/prompts/author_profile.md（合成模板 v1）。严格按模板的「输出（严格 JSON）」与「硬约束」为**该文件中的每一位作者**合成画像。

输出：单个 JSON 对象：{"author_snapshots": [ {author_id, focus, summary, key_contributions, risks, representative_paper_ids}, ... ]}。
- author_id 必须来自输入的 authors 数组；representative_paper_ids 必须是该作者 papers 中的 paper_id（2-5 篇）。
- risks 字段：无 L0/L1 标记写「无风险标记」；有标记逐字转述（不得定性）。
- 只输出 JSON，不要其他文字。`, { label: `pack-${p.split("/").pop()}`, phase: "作者级画像合成（并行）" })
));

const authorSnapshots = [];
for (const s of out) {
  let obj = null;
  try { obj = typeof s === "string" ? JSON.parse(s) : s; } catch { obj = null; }
  if (obj && Array.isArray(obj.author_snapshots)) authorSnapshots.push(...obj.author_snapshots);
}
return {
  domain,
  prompt_ver: "author_profile.md@v1-20260827",
  model: "peopar-agent@2026-08-27 (workflow fan-out)",
  author_snapshots: authorSnapshots,
  produced: authorSnapshots.length,
  save_to: `data/${domain}_authors_batch.json`,
};
