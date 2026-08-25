#!/bin/zsh
# 增量更新：免费源（PubMed + OpenAlex）+ 图谱刷新
# 建议周期：每日（可由系统定时任务触发）
cd "$(dirname "$0")"
for d in neuroling ad_lesne; do
  echo "=== 域 $d: PubMed 增量 ==="
  python3 ingest/pubmed.py "$d" --incremental
  echo "=== 域 $d: OpenAlex 回填 ==="
  python3 ingest/openalex.py "$d"
  echo "=== 域 $d: 图谱刷新 ==="
  python3 analyze/graph.py "$d"
done
echo "更新完成：$(date)"
