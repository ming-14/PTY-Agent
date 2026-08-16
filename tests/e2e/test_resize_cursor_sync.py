# -*- coding: utf-8 -*-
"""E2E 测试：resize 后 snapshot 光标位置与 ConPTY 真实光标位置一致。

核心不变量：resize_complete 的 snapshot 末尾光标定位（CSI row;colH）必须
等于 ConPTY 实际光标位置（通过按键回显中的 CUP 序列实测）。
违背此不变量 → ConPTY 后续的绝对光标定位落在前端显示内容中间
（"光标在 dir 输出中间" bug，根因：旧 Grid.reflow 锚底语义 vs ConPTY 锚顶语义）。

场景（覆盖两个方向 + 满内容 + grow 回退）：
  1. grow       80x24 → 120x30（底部补空行，光标不动）
  2. shrink     120x30 → 80x24（砍底部空行，光标不动）
  3. 满内容shrink 80x24 → 80x18（顶部行推 scrollback，光标随内容上移）
  4. grow 回    80x18 → 80x24（底部补空行，光标不动）

前置条件：守护进程运行中（python app.py daemon 或 python -m src start）。
守护进程不在时整个模块 skip（不失败）。

运行：
  python -m pytest tests/e2e/test_resize_cursor_sync.py -v
  或直接：python tests/e2e/test_resize_cursor_sync.py
"""

import asyncio
import json
import re
import socket
import sys
import time
import uuid

import pytest

websockets = pytest.importorskip("websockets", reason="需要 websockets 库")

_WS_URL = "ws://localhost:18766/ws"
CSI_CUP_RE = re.compile(r"\x1b\[(\d+);(\d+)H")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")


def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def _daemon_alive() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 18766), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _daemon_alive(),
    reason="守护进程未运行（python -m src start），跳过 e2e")


# ── WS 工具 ─────────────────────────────────────────────────

async def _recv_until(ws, pred, timeout=5.0, collect=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
        except asyncio.TimeoutError:
            break
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if collect is not None and msg.get("type") == "output":
            collect.append(msg.get("data", ""))
        if pred(msg):
            return msg
    return None


def _parse_rows(snapshot: str) -> dict:
    """{1-based row: text}，同行多次 CUP 时合并非空内容"""
    rows = {}
    parts = CSI_CUP_RE.split(snapshot)
    for i in range(1, len(parts) - 2, 3):
        r, text = int(parts[i]), _strip_ansi(parts[i + 2])
        if r not in rows or text.strip():
            rows[r] = rows.get(r, "") + text
    return rows


# ── 测试体 ──────────────────────────────────────────────────

# 平台化：Windows 用 cmd（> prompt），Unix 用 bash（$ prompt）
_IS_WIN = sys.platform == "win32"
_SHELL_CMD = "cmd.exe" if _IS_WIN else "bash"
_PROMPT_CHAR = ">" if _IS_WIN else "$"
_FILL_CMD = (
    "for /l %i in (1,1,40) do @echo LINE%i-aaaaaaaaaaaaaaaaaaaa\r"
    if _IS_WIN
    else 'for i in $(seq 1 40); do echo LINE$i-aaaaaaaaaaaaaaaaaaaa; done\r'
)


class _Session:
    """一个临时 shell 会话：填充确定性内容（产生 scrollback）"""

    def __init__(self):
        self.sid = "e2esync_" + uuid.uuid4().hex[:8]
        self.ws = None

    async def __aenter__(self):
        self.ws = await websockets.connect(
            _WS_URL, max_size=8 * 1024 * 1024, ping_interval=20)
        await self.ws.send(json.dumps({
            "type": "create", "session_id": self.sid, "command": _SHELL_CMD,
            "pty": True, "cols": 80, "rows": 24}))
        sub = await _recv_until(
            self.ws, lambda m: m.get("type") == "subscribed"
            and m.get("sessionId") == self.sid, timeout=10.0)
        assert sub, "未收到 subscribed"
        await asyncio.sleep(1.5)
        # 40 行确定性输出（< 60 列，不触发 rewrap）
        await self.ws.send(json.dumps({
            "type": "input", "session_id": self.sid, "data": _FILL_CMD}))
        await asyncio.sleep(3.0)
        await _recv_until(self.ws, lambda m: False, timeout=0.5, collect=[])
        return self

    async def __aexit__(self, *exc):
        try:
            await self.ws.send(json.dumps({"type": "kill", "session_id": self.sid}))
            await asyncio.sleep(0.3)
            await self.ws.close()
        except Exception:
            pass

    async def resize_and_check(self, cols: int, rows: int, tag: str) -> None:
        """resize 后断言：snapshot 光标 == ConPTY 实测光标，且该行是 prompt"""
        await self.ws.send(json.dumps({
            "type": "resize", "session_id": self.sid, "cols": cols, "rows": rows}))
        rc = await _recv_until(
            self.ws, lambda m: m.get("type") == "resize_complete"
            and m.get("sessionId") == self.sid, timeout=5.0)
        assert rc, f"[{tag}] 未收到 resize_complete"
        snapshot = rc.get("snapshot", "")
        assert snapshot, f"[{tag}] snapshot 为空"

        # A. snapshot 末尾 cursor 定位
        cups = list(CSI_CUP_RE.finditer(snapshot))
        assert cups, f"[{tag}] snapshot 无光标定位序列"
        snap_pos = (int(cups[-1].group(1)), int(cups[-1].group(2)))

        # B. ConPTY 实测光标：按键 'X'，回显中 'X' 前的 CUP
        echo = []
        await self.ws.send(json.dumps({
            "type": "input", "session_id": self.sid, "data": "X"}))
        await _recv_until(self.ws, lambda m: False, timeout=1.5, collect=echo)
        blob = "".join(echo)
        idx = blob.find("X")
        ecups = list(CSI_CUP_RE.finditer(blob[:idx if idx >= 0 else len(blob)]))
        assert ecups, f"[{tag}] 回显中无 CUP: {blob!r}"
        pty_pos = (int(ecups[-1].group(1)), int(ecups[-1].group(2)))
        # 清理输入的 X，避免污染后续场景
        await self.ws.send(json.dumps({
            "type": "input", "session_id": self.sid, "data": "\x7f"}))
        await asyncio.sleep(0.5)

        # C. snapshot 中光标所在行应为 prompt
        cursor_line = _parse_rows(snapshot).get(snap_pos[0], "")

        assert snap_pos == pty_pos, (
            f"[{tag}] snapshot 光标 {snap_pos} != ConPTY 实测 {pty_pos}，"
            f"按键会回显在显示内容中间")
        assert _PROMPT_CHAR in cursor_line, (
            f"[{tag}] 光标行不是 prompt: {cursor_line!r}")


async def _run_all_scenarios():
    async with _Session() as s:
        await s.resize_and_check(120, 30, "grow 80x24→120x30")
        await s.resize_and_check(80, 24, "shrink 120x30→80x24")
        await s.resize_and_check(80, 18, "满内容shrink 80x24→80x18")
        await s.resize_and_check(80, 24, "grow回 80x18→80x24")


def test_resize_cursor_sync():
    """四个 resize 场景的光标一致性断言（同步驱动事件循环）"""
    asyncio.run(_run_all_scenarios())


if __name__ == "__main__":
    if not _daemon_alive():
        print("[SKIP] 守护进程未运行（python -m src start）")
        sys.exit(0)
    asyncio.run(_run_all_scenarios())
    print("[PASS] resize 光标一致性全部通过")
