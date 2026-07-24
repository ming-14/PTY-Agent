"""验证 VNC 进程是否正确绑定到 Job Object（守护进程意外结束时 VNC 不残留）。

流程：
1. 连接守护进程 WS，发送 vnc_start，获取 vnc_pid
2. 用 IsProcessInJob 检查 winvnc.exe 进程是否在 Job 中
3. 进一步：检查 winvnc.exe 的所有子进程是否也在 Job 中
4. 发送 vnc_stop 清理

用法：python tests/e2e/test_vnc_job.py
"""
import asyncio
import ctypes
import ctypes.wintypes
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import websockets

WS_URL = "ws://127.0.0.1:18766/ws"

_kernel32 = ctypes.windll.kernel32
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
_kernel32.IsProcessInJob.restype = ctypes.wintypes.BOOL
_kernel32.IsProcessInJob.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.wintypes.BOOL)]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

# PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010


def is_process_in_job(pid: int) -> bool:
    """检查指定 pid 是否在任何 Job 中。"""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
    if not handle:
        return False
    try:
        result = ctypes.wintypes.BOOL(False)
        # 第二个参数为 NULL，表示检查是否在任意 Job 中
        ok = _kernel32.IsProcessInJob(handle, None, ctypes.byref(result))
        if not ok:
            return False
        return bool(result.value)
    finally:
        _kernel32.CloseHandle(handle)


def list_child_pids(parent_pid: int):
    """用 wmic 列出指定父 pid 的所有直接子进程。"""
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", f"ParentProcessId={parent_pid}",
             "get", "ProcessId", "/format:csv"],
            timeout=10, text=True
        ).strip()
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("node"):
            continue
        parts = line.split(",")
        if parts:
            try:
                pids.append(int(parts[-1]))
            except ValueError:
                pass
    return pids


async def recv_json(ws, expected_type, timeout=60.0):
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    msg = json.loads(raw)
    assert msg.get("type") == expected_type, f"期望 {expected_type}，实际 {msg.get('type')}: {msg}"
    return msg


async def main():
    print(f"[1] 连接 {WS_URL}")
    async with websockets.connect(WS_URL, max_size=8 * 1024 * 1024) as ws:
        print("[2] 发送 vnc_start")
        await ws.send(json.dumps({"type": "vnc_start"}))
        msg = await recv_json(ws, "vnc_started", timeout=60.0)
        vnc_pid = msg.get("vnc_pid")
        vnc_port = msg.get("vnc_port")
        print(f"    vnc_started: pid={vnc_pid} port={vnc_port}")

        # 等待 winvnc 完全启动（可能有子进程）
        await asyncio.sleep(2)

        print(f"[3] 检查 winvnc 主进程 pid={vnc_pid} 是否在 Job 中")
        in_job = is_process_in_job(vnc_pid)
        print(f"    IsProcessInJob(pid={vnc_pid}) = {in_job}")
        if in_job:
            print("    [OK] winvnc 主进程已绑定到 Job Object")
        else:
            print("    [FAIL] winvnc 主进程未绑定到 Job Object！守护进程意外退出会残留")

        print(f"[4] 检查 winvnc 的子进程是否在 Job 中")
        child_pids = list_child_pids(vnc_pid)
        print(f"    子进程 pids: {child_pids}")
        all_ok = in_job
        for cpid in child_pids:
            cin = is_process_in_job(cpid)
            print(f"    IsProcessInJob(pid={cpid}) = {cin}")
            if not cin:
                all_ok = False

        print("\n" + "=" * 50)
        if all_ok:
            print("[完成] 所有 VNC 相关进程均绑定到 Job Object")
        else:
            print("[警告] 部分 VNC 进程未绑定到 Job Object")
        print("=" * 50)

        # 停止 VNC
        print("\n[5] 发送 vnc_stop")
        await ws.send(json.dumps({"type": "vnc_stop"}))
        try:
            await recv_json(ws, "vnc_stopped", timeout=15.0)
            print("    vnc_stopped")
        except Exception as e:
            print(f"    [WARN] 等待 vnc_stopped 失败: {e}")

        return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.get_event_loop().run_until_complete(main()))
