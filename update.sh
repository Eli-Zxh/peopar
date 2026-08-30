#!/bin/zsh
# 增量更新：免费源（PubMed 增量 + OpenAlex 回填 + 图谱刷新 + 失效感知）+ vault 投影导出
# 建议周期：每日（可由系统定时任务触发，模板见 misc/com.peopar.update.plist）
# 注意：首次全量/历史修复用 python3 ingest/pubmed.py <域> --full（EDAT 年份窗口分片）
# vault 路径可用环境变量 PEOPAR_VAULT 覆盖（Obsidian 插件读 vault 内 peopar/ 快照）
VAULT="${PEOPAR_VAULT:-$HOME/Documents/Obisidian Valut}"
cd "$(dirname "$0")"
for d in neuroling ad_lesne; do
  echo "=== 域 $d: PubMed 增量 ==="
  python3 ingest/pubmed.py "$d" --incremental
  echo "=== 域 $d: OpenAlex 回填 ==="
  python3 ingest/openalex.py "$d"
  echo "=== 域 $d: 图谱刷新 ==="
  python3 analyze/graph.py "$d"
  echo "=== 域 $d: 快照失效感知（不自动重合成）==="
  python3 manage/snapshot.py staleness "$d"
done
echo "=== 导出 vault 投影（Obsidian 插件离线数据）==="
python3 manage/export_vault.py "$VAULT" --topic "${PEOPAR_TOPIC:-神经语言学与失语症}" --domain neuroling,ad_lesne --researcher-min-papers 3
echo "更新完成：$(date)"
