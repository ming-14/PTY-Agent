#!/bin/bash
# start.sh - PTY-Agent Linux 启动脚本
# 使用方法: ./start.sh [--port PORT]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"

# 解析参数
PORT=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            PORT="$2"
            shift 2
            ;;
        *)
            echo "用法: $0 [--port PORT]"
            exit 1
            ;;
    esac
done

echo "=== PTY-Agent Linux 启动 ==="

# 停止已有的守护进程
echo "[1/3] 检查并停止已有的守护进程..."
if python -m src stop --force 2>/dev/null; then
    echo "   已停止旧的守护进程"
else
    echo "   没有运行中的守护进程"
fi

# 启动守护进程
echo "[2/3] 启动守护进程..."
if [ -n "$PORT" ]; then
    python -m src start --port "$PORT"
else
    python -m src start
fi

# 验证运行状态
echo "[3/3] 验证运行状态..."
sleep 1
if python -m src status 2>/dev/null | grep -q "running"; then
    echo ""
    echo "=== PTY-Agent 启动成功 ==="
    echo ""
    echo "常用命令:"
    echo "  启动守护进程:  python -m src start"
    echo "  停止守护进程:  python -m src stop"
    echo "  执行命令:      python app.py exec <id> -c \"<command>\""
    echo "  发送输入:      python app.py send <id> \"<input>\""
    echo "  读取输出:      python app.py read <id>"
    echo "  列出会话:      python app.py list"
    echo "  终止会话:      python app.py kill <id>"
    echo "  查看事件:      python app.py events <id>"
else
    echo ""
    echo "=== PTY-Agent 启动可能失败，请检查日志 ==="
fi