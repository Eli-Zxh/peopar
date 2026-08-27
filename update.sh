#!/bin/zsh
# 增量更新：免费源（PubMed 增量 + OpenAlex 回填 + 图谱刷新 + 失效感知）
# 建议周期：每日（可由系统定时任务触发，模板见 misc/com.peopar.update.plist）
# 注意：首次全量/历史修复用 python3 ingest/pubmed.py <域> --full（EDAT 年份窗口分片）
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
echo "更新完成：$(date)"
