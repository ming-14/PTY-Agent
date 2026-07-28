#!/bin/bash
# stop.sh - PTY-Agent Linux 停止脚本
# 使用方法: ./stop.sh [--force]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

FORCE=""
if [[ "$1" == "--force" ]]; then
    FORCE="--force"
fi

echo "=== 停止 PTY-Agent 守护进程 ==="
python -m src stop $FORCE