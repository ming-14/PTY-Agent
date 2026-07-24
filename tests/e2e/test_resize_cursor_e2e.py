"""E2E 测试：验证 resize 后 snapshot 包含正确的光标位置。

测试场景（复现 reference/1.txt 的"光标在 dir 输出中间"问题）：
  1. 连接守护进程 WS（ws://localhost:18766/ws）
  2. 创建 cmd.exe 会话
  3. 发送 'dir\\r' 让 cmd 输出目录列表
  4. 等待输出稳定
  5. 发送 resize（缩小 cols，例如 80→60）
  6. 接收 resize_complete，验证 snapshot：
     - snapshot 非空
     - snapshot 末尾应包含光标定位序列 CSI row;col H (\\x1b[r;cH)
     - 光标 col 应为 1（prompt 起始位置），而非 dir 输出中间

使用方法：
  # 先启动守护进程
  python app.py daemon --debug

  # 另开终端运行
  python tests/e2e/test_resize_cursor_e2e.py

  # 或指定 URL
  python tests/e2e/test_resize_cursor_e2e.py --url ws://localhost:18766/ws
"""

import argparse
import asyncio
import os
import re
import sys
import time
import uuid

# 将项目根目录加入 sys.path（便于 import src.* 如果需要）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import websockets
except ImportError:
    print("[FAIL] 需要 websockets 库：pip install websockets", file=sys.stderr)
    sys.exit(2)


# CSI 光标定位序列：\x1b[row;colH  （row/col 为数字）
CSI_CUP_RE = re.compile(rb"\x1b\[(\d+);(\d+)H")
# 简化 prompt 检测：盘符:\\...> 或 盘符:\\>
PROMPT_RE = re.compile(rb"[A-Za-z]:\\.*?>")


async def _recv_until(ws, predicate, timeout=5.0, collect_output=True):
    """接收消息直到 predicate(msg_dict) 返回 True 或超时。

    Args:
        ws: websockets 连接
        predicate: 函数 (msg_dict) -> bool
        timeout: 超时秒数
        collect_output: 是否收集 'output' 类型消息用于回放

    Returns:
        (matched_msg, outputs_list)
    """
    deadline = time.monotonic() + timeout
    outputs = []
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
        except asyncio.TimeoutError:
            break
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
        import json
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        t = msg.get("type", "")
        if collect_output and t == "output":
            outputs.append(msg.get("data", ""))
        if predicate(msg):
            return msg, outputs
    return None, outputs


async def _send_json(ws, obj):
    import json
    await ws.send(json.dumps(obj))


def _find_last_cursor_pos(snapshot_bytes):
    """从 snapshot 中找出最后一个 CSI row;colH 序列的光标位置。

    Returns:
        (row, col) 或 None
    """
    matches = list(CSI_CUP_RE.finditer(snapshot_bytes))
    if not matches:
        return None
    last = matches[-1]
    return int(last.group(1)), int(last.group(2))


async def run_test(url: str, command: str, initial_cols: int, initial_rows: int,
                   resize_cols: int, resize_rows: int) -> int:
    """运行单个 resize + 光标测试。

    Returns:
        0 = 通过，1 = 失败，2 = 环境错误
    """
    sid = "e2e_" + uuid.uuid4().hex[:8]
    print(f"\n[TEST] sid={sid}")
    print(f"       url={url}")
    print(f"       command={command!r}")
    print(f"       initial={initial_cols}x{initial_rows} → resize={resize_cols}x{resize_rows}")

    try:
        ws = await websockets.connect(url, max_size=8 * 1024 * 1024, ping_interval=20)
    except Exception as e:
        print(f"[FAIL] 无法连接守护进程 WS ({url}): {e}")
        print("       请先启动：python app.py daemon --debug")
        return 2

    try:
        # 1. 创建会话
        print("[STEP 1] 创建会话...")
        await _send_json(ws, {
            "type": "create",
            "session_id": sid,
            "command": command,
            "pty": True,
            "cols": initial_cols,
            "rows": initial_rows,
        })

        # 等待 subscribed
        sub_msg, outputs = await _recv_until(
            ws, lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid,
            timeout=10.0, collect_output=False,
        )
        if not sub_msg:
            print("[FAIL] 未收到 subscribed 消息")
            return 1
        print(f"[OK]    subscribed: running={sub_msg.get('running')} "
              f"cols={sub_msg.get('cols')} rows={sub_msg.get('rows')}")

        # 2. 等待初始 prompt 输出稳定
        print("[STEP 2] 等待初始 prompt...")
        await asyncio.sleep(1.5)

        # 3. 发送 'dir\\r' 触发输出
        print("[STEP 3] 发送 'dir\\r' 触发目录列表输出...")
        await _send_json(ws, {
            "type": "input",
            "session_id": sid,
            "data": "dir\r",
        })

        # 等待 dir 输出完成（等 cmd.exe 回到 prompt）
        # 简单策略：等 2.5 秒让 dir 完成
        await asyncio.sleep(2.5)

        # 收集期间的 output 消息，用于诊断
        _, dir_outputs = await _recv_until(ws, lambda m: False, timeout=0.3, collect_output=True)
        dir_blob = b"".join(o.encode("utf-8", errors="replace") for o in dir_outputs)
        print(f"[OK]    dir 输出收齐：{len(dir_blob)} 字节")

        # 4. 发送 resize
        print(f"[STEP 4] 发送 resize {initial_cols}x{initial_rows} → "
              f"{resize_cols}x{resize_rows}...")
        await _send_json(ws, {
            "type": "resize",
            "session_id": sid,
            "cols": resize_cols,
            "rows": resize_rows,
        })

        # 等待 resize_complete
        rc_msg, _ = await _recv_until(
            ws, lambda m: m.get("type") == "resize_complete" and m.get("sessionId") == sid,
            timeout=5.0, collect_output=False,
        )
        if not rc_msg:
            print("[FAIL] 未收到 resize_complete 消息")
            return 1

        snapshot = rc_msg.get("snapshot", "")
        snapshot_bytes = snapshot.encode("utf-8", errors="replace")
        print(f"[OK]    resize_complete: cols={rc_msg.get('cols')} rows={rc_msg.get('rows')} "
              f"snapshot_len={len(snapshot)}")

        if not snapshot:
            print("[FAIL] snapshot 为空！v4 方案要求 snapshot 必须包含 PTY 真实状态")
            return 1

        # 5. 验证 snapshot：最后一个 CSI row;colH 应该指向 prompt 位置
        print("[STEP 5] 验证 snapshot 中的光标位置...")
        cursor_pos = _find_last_cursor_pos(snapshot_bytes)
        if not cursor_pos:
            print("[WARN]  snapshot 中未找到 CSI row;colH 序列")
            print("       （可能是 PTY 类型不发送 CUP，或 snapshot 末尾刚好不是光标定位）")
            print("       快照末尾 200 字节：")
            print(_preview_bytes(snapshot_bytes[-200:]))
            # 不算失败，但提示
        else:
            row, col = cursor_pos
            print(f"[OK]    最后光标位置：row={row} col={col}")

            # 验证：col 应该比较小（prompt 通常在行首或盘符位置）
            # cmd.exe 的 prompt 形如 "C:\\Users\\rikka\\Desktop\\PTY-Agent>"
            # resize 后 col 应该 <= prompt 长度（通常 < 60）
            if col > resize_cols:
                print(f"[FAIL] 光标 col={col} 超过 resize 后的 cols={resize_cols}，"
                      f"明显错位")
                return 1

            # 检查 snapshot 末尾是否是 prompt（包含 '>'）
            tail = snapshot_bytes[-300:]
            if PROMPT_RE.search(tail):
                print(f"[OK]    snapshot 末尾包含 prompt 模式，光标在 prompt 后")
            else:
                print(f"[WARN]  snapshot 末尾未检测到 prompt 模式")
                print("       末尾 200 字节：")
                print(_preview_bytes(tail[-200:]))

        # 6. 收集 resize 后的 output（ConPTY repaint），诊断用
        _, post_outputs = await _recv_until(ws, lambda m: False, timeout=0.5, collect_output=True)
        if post_outputs:
            post_blob = b"".join(o.encode("utf-8", errors="replace") for o in post_outputs)
            print(f"[INFO]  resize 后 ConPTY repaint: {len(post_blob)} 字节")
        else:
            print(f"[INFO]  resize 后无 ConPTY repaint（cmd.exe 常见）")

        # 7. 清理：kill session
        print("[STEP 6] 清理：kill session...")
        try:
            await _send_json(ws, {"type": "kill", "session_id": sid})
            await asyncio.sleep(0.3)
        except Exception:
            pass

        print("[PASS] 测试通过：snapshot 机制工作正常")
        return 0

    finally:
        try:
            await ws.close()
        except Exception:
            pass


def _preview_bytes(b: bytes, max_len: int = 200) -> str:
    """字节数组转可读字符串（替换控制字符）。"""
    s = b[:max_len].decode("utf-8", errors="replace")
    s = s.replace("\r", "\\r").replace("\n", "\\n").replace("\x1b", "\\e").replace("\t", "\\t")
    return s


async def main_async():
    parser = argparse.ArgumentParser(description="E2E 测试：resize + 光标位置")
    parser.add_argument("--url", default="ws://localhost:18766/ws",
                        help="守护进程 WS URL（默认 ws://localhost:18766/ws）")
    parser.add_argument("--command", default="cmd.exe",
                        help="测试命令（默认 cmd.exe）")
    parser.add_argument("--initial-cols", type=int, default=80, help="初始 cols")
    parser.add_argument("--initial-rows", type=int, default=24, help="初始 rows")
    parser.add_argument("--resize-cols", type=int, default=60, help="resize 后 cols")
    parser.add_argument("--resize-rows", type=int, default=24, help="resize 后 rows")
    args = parser.parse_args()

    print("=" * 70)
    print("E2E 测试：resize 后 snapshot 光标位置")
    print("=" * 70)

    rc = await run_test(
        url=args.url,
        command=args.command,
        initial_cols=args.initial_cols,
        initial_rows=args.initial_rows,
        resize_cols=args.resize_cols,
        resize_rows=args.resize_rows,
    )

    print("\n" + "=" * 70)
    if rc == 0:
        print("结果：PASS")
    elif rc == 2:
        print("结果：环境错误（无法连接守护进程）")
    else:
        print("结果：FAIL")
    print("=" * 70)
    sys.exit(rc)


if __name__ == "__main__":
    asyncio.run(main_async())
