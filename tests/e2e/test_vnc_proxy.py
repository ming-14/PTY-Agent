"""VNC 端口统一验证：通过守护进程 /vnc/websockify 代理连接 VNC。

流程：
1. 连接守护进程 WS（ws://127.0.0.1:18766/ws）
2. 发送 vnc_start，等待 vnc_started 响应（含 vnc_port/password）
3. 连接 ws://127.0.0.1:18766/vnc/websockify
4. 验证能收到 RFB 协议握手首字节 "RFB 003.008"
5. 发送 vnc_stop 清理

用法：python tests/e2e/test_vnc_proxy.py
"""
import asyncio
import json
import sys
from pathlib import Path

# 将 src 加入 path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Windows 专属用例（winvnc）；非 Windows 平台直接跳过
if sys.platform != "win32":
    print("VNC proxy e2e 仅支持 Windows，跳过")
    sys.exit(0)

import websockets


WS_URL = "ws://127.0.0.1:18766/ws"
PROXY_URL = "ws://127.0.0.1:18766/vnc/websockify"


async def recv_json(ws, expected_type, timeout=30.0):
    """接收一条 WS 消息并解析 JSON，校验 type 字段。"""
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    msg = json.loads(raw)
    assert msg.get("type") == expected_type, (
        f"期望 {expected_type}，实际收到 {msg.get('type')}: {msg}"
    )
    return msg


async def main():
    print(f"[1] 连接 {WS_URL}")
    async with websockets.connect(WS_URL, max_size=8 * 1024 * 1024) as ws:
        # 发送 vnc_start
        print("[2] 发送 vnc_start")
        await ws.send(json.dumps({"type": "vnc_start"}))

        # 等待 vnc_started
        print("[3] 等待 vnc_started...")
        msg = await recv_json(ws, "vnc_started", timeout=60.0)
        vnc_port = msg.get("vnc_port")
        password = msg.get("password")
        vnc_pid = msg.get("vnc_pid")
        print(f"    vnc_started: port={vnc_port} pid={vnc_pid} password={password}")

        # 测试代理端点
        print(f"[4] 连接代理端点 {PROXY_URL}")
        try:
            async with websockets.connect(PROXY_URL, max_size=8 * 1024 * 1024) as proxy:
                # VNC 服务端首先发送 RFB 协议版本号：RFB 003.008\n (12 字节)
                print("[5] 等待 RFB 协议握手...")
                data = await asyncio.wait_for(proxy.recv(), timeout=10.0)
                if isinstance(data, str):
                    data = data.encode("utf-8")
                print(f"    收到 {len(data)} 字节: {data!r}")
                if data.startswith(b"RFB "):
                    print("[OK] 代理握手成功：收到 RFB 协议首字节")
                else:
                    print(f"[FAIL] 未收到 RFB 协议首字节，实际收到: {data!r}")
                    return 1
        except Exception as e:
            print(f"[FAIL] 代理连接失败: {e}")
            return 1

        # 停止 VNC
        print("[6] 发送 vnc_stop")
        await ws.send(json.dumps({"type": "vnc_stop"}))
        try:
            await recv_json(ws, "vnc_stopped", timeout=15.0)
            print("    vnc_stopped")
        except Exception as e:
            print(f"    [WARN] 等待 vnc_stopped 失败: {e}")

        print("\n[完成] VNC 端口统一验证通过")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.get_event_loop().run_until_complete(main()))
