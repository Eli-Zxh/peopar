#!/bin/zsh
# webvpn 半自动导入：导入用户在机构 webvpn 门户导出的题录文件 → 图谱刷新 → 失效感知
# 用法：
#   ./update_webvpn.sh <域id> <题录文件> --source scopus|cnki|wanfang [--query "检索式"]
# 前置：用户已在 webvpn 门户登录并导出文件（见 prompts/webvpn_collect.md）
cd "$(dirname "$0")"
if [ $# -lt 3 ]; then
  echo "用法: $0 <域id> <文件> --source scopus|cnki|wanfang [--query \"检索式\"]"
  exit 1
fi
DOMAIN="$1"; FILE="$2"; shift 2
echo "=== webvpn 导入：$DOMAIN ← $FILE ==="
python3 manage/webvpn.py import "$FILE" --domain "$DOMAIN" "$@" || exit 1
echo "=== 图谱刷新 ==="
python3 analyze/graph.py "$DOMAIN" || exit 1
echo "=== 失效感知 ==="
python3 manage/snapshot.py staleness "$DOMAIN"
echo "webvpn 更新完成：$(date)"
