"""最终验证：模拟守护进程意外结束（taskkill /F），检查 VNC 进程是否被 Job Object 清理。

流程：
1. 连接守护进程 WS，发送 vnc_start，获取 vnc_pid
2. 获取守护进程 pid（通过端口 18766）
3. taskkill /F 守护进程（模拟意外崩溃）
4. 等待 3 秒，让 OS 关闭 Job Object 句柄并清理子进程
5. 检查 vnc_pid 是否还在运行
6. 报告结果

用法：python tests/e2e/test_vnc_job_kill.py
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Windows 专属用例（taskkill/winvnc/Job Object）；非 Windows 平台直接跳过
if sys.platform != "win32":
    print("VNC Job kill e2e 仅支持 Windows，跳过")
    import pytest

    pytest.skip("仅支持 Windows", allow_module_level=True)

import websockets

WS_URL = "ws://127.0.0.1:18766/ws"


async def recv_json(ws, expected_type, timeout=60.0):
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    msg = json.loads(raw)
    assert msg.get("type") == expected_type, f"期望 {expected_type}，实际 {msg.get('type')}: {msg}"
    return msg


def is_pid_alive(pid: int) -> bool:
    """检查 pid 是否还在运行。"""
    try:
        subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            timeout=5, text=True
        )
        # tasklist 总是返回 0，需要检查输出内容
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            timeout=5, text=True
        ).strip()
        return str(pid) in out and "INFO:" not in out
    except Exception:
        return False


def get_daemon_pid() -> int:
    """通过端口 18766 获取守护进程 pid。"""
    out = subprocess.check_output(
        ["netstat", "-ano", "-p", "tcp"], timeout=5, text=True
    )
    for line in out.splitlines():
        if "18766" in line and "LISTENING" in line.upper():
            parts = line.split()
            return int(parts[-1])
    raise RuntimeError("daemon pid not found on port 18766")


async def main():
    print(f"[1] 连接 {WS_URL}")
    async with websockets.connect(WS_URL, max_size=8 * 1024 * 1024) as ws:
        print("[2] 发送 vnc_start")
        await ws.send(json.dumps({"type": "vnc_start"}))
        msg = await recv_json(ws, "vnc_started", timeout=60.0)
        vnc_pid = msg.get("vnc_pid")
        vnc_port = msg.get("vnc_port")
        print(f"    vnc_started: pid={vnc_pid} port={vnc_port}")

        # 确认 VNC 进程在运行
        await asyncio.sleep(1)
        if not is_pid_alive(vnc_pid):
            print("[FAIL] VNC 进程未运行")
            return 1
        print(f"    [确认] VNC 进程 pid={vnc_pid} 正在运行")

    # WS 已关闭，获取守护进程 pid
    daemon_pid = get_daemon_pid()
    print(f"[3] 守护进程 pid={daemon_pid}")

    # 强制 kill 守护进程（模拟意外崩溃）
    print(f"[4] taskkill /F /PID {daemon_pid}（模拟意外崩溃）")
    subprocess.run(["taskkill", "/F", "/PID", str(daemon_pid)],
                   timeout=10, capture_output=True)
    # 不发 vnc_stop，让守护进程直接死掉

    # 等待 OS 关闭 Job Object 句柄并清理子进程
    print("[5] 等待 3 秒，让 OS 清理 Job Object 子进程...")
    await asyncio.sleep(3)

    # 检查 VNC 进程是否被清理
    print(f"[6] 检查 VNC 进程 pid={vnc_pid} 是否被清理")
    if is_pid_alive(vnc_pid):
        print(f"    [FAIL] VNC 进程 pid={vnc_pid} 仍在运行！Job Object 未生效")
        # 手动清理残留
        subprocess.run(["taskkill", "/F", "/PID", str(vnc_pid)],
                       timeout=10, capture_output=True)
        return 1
    else:
        print(f"    [OK] VNC 进程 pid={vnc_pid} 已被 Job Object 自动清理")
        print("\n" + "=" * 60)
        print("[完成] 守护进程意外结束后 VNC 进程被正确清理")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.get_event_loop().run_until_complete(main()))
