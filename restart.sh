#!/bin/bash
# restart.sh - PTY-Agent Linux 重启脚本
# 使用方法: ./restart.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 重启 PTY-Agent ==="
python -m src stop --force 2>/dev/null || true
sleep 1
python -m src start
echo "=== 重启完成 ==="