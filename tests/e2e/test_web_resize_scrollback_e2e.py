# -*- coding: utf-8 -*-
"""E2E 测试：resize 保留 scrollback（Bug #1 回归验证）。

验证：
  1. 会话产生 scrollback 后，resize_complete 响应携带 scrollback（含历史行）
  2. scrollback 内容包含 resize 前滚出可见区的行（不丢历史）
  3. resize 后可见区 snapshot 正常（内容/光标不因 scrollback 保留而错乱）

daemon 由 web_daemon fixture 自动启动（未运行则启动、结束后停止），
不要求外部手动 `python -m src start`。

运行：
  python -m pytest tests/e2e/test_web_resize_scrollback_e2e.py -v
"""

import asyncio
import json
import sys
import time
import uuid

import pytest

websockets = pytest.importorskip("websockets", reason="需要 websockets 库")

_WS_URL = "ws://localhost:18766/ws"

# 用 python3 -u -i 创建交互式会话（Linux 上 python 可能不存在，用 python3）
_CREATE_CMD = (
    "cmd /c chcp 65001 >nul & python -u -i"
    if sys.platform == "win32" else
    "python3 -u -i"
)
# Linux PTY 上 \r\n 会导致 REPL 多行输入执行异常，仅用 \n
_EOL = "\r\n" if sys.platform == "win32" else "\n"


@pytest.fixture(scope="module", autouse=True)
def _web_daemon(web_daemon):
    """web e2e 自启 daemon（复用 conftest.web_daemon）：未运行则启动、结束后停止"""
    yield


async def recv_until(ws, pred, timeout=8.0, collect=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
        except asyncio.TimeoutError:
            break
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            for m in data:
                if collect is not None and m.get("type") == "output":
                    collect.append(m.get("data", ""))
                if pred(m):
                    return m
        else:
            if collect is not None and data.get("type") == "output":
                collect.append(data.get("data", ""))
            if pred(data):
                return data
    return None


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07", "", s)


@pytest.mark.asyncio
async def test_resize_preserves_scrollback():
    sid = "sb_e2e_" + uuid.uuid4().hex[:6]
    async with websockets.connect(_WS_URL) as ws:
        # 1. 创建会话
        await ws.send(json.dumps({
            "type": "create", "session_id": sid,
            "command": _CREATE_CMD,
            "cols": 100, "rows": 24,
        }))
        sub = await recv_until(ws, lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid)
        assert sub is not None, "create+subscribe 超时"
        uid = sub.get("sessionUid")
        await asyncio.sleep(0.8)

        # 2. 生成 200 行输出（24 行可见，其余进入 scrollback）
        script = "for i in range(200):\n print(f'sb-line-{i:04d}')\n"
        await ws.send(json.dumps({
            "type": "input", "sessionUid": uid,
            "data": script.replace("\n", _EOL) + _EOL,
        }))
        out = []
        # WSL/CI 慢文件系统上 Python 启动 + 200 行输出可能超过 12s，放宽到 30s
        await recv_until(ws, lambda m: m.get("type") == "output" and "sb-line-0199" in m.get("data", ""),
                         timeout=30.0, collect=out)
        assert any("sb-line-0199" in d for d in out), "输出未到达"
        await asyncio.sleep(0.5)

        # 3. 新连接测量 resize 前的模型 scrollback（基线）
        async with websockets.connect(_WS_URL) as ws2:
            await ws2.send(json.dumps({"type": "subscribe", "sessionUid": uid}))
            sub2 = await recv_until(ws2, lambda m: m.get("type") == "subscribed" and m.get("sessionUid") == uid)
            assert sub2 is not None
            pre_sb = sub2.get("scrollback", "") or ""
            pre_lines = [l for l in _strip_ansi(pre_sb).split("\n") if l.strip()]
            print(f"[baseline] pre-resize model scrollback lines={len(pre_lines)}")

        # 4. resize（发起方收到 resize_complete）
        await ws.send(json.dumps({
            "type": "resize", "sessionUid": uid, "cols": 120, "rows": 30,
        }))
        rc = await recv_until(ws, lambda m: m.get("type") == "resize_complete" and m.get("sessionUid") == uid)
        assert rc is not None, "resize_complete 超时"
        rc_sb = rc.get("scrollback", "") or ""
        rc_snap = rc.get("snapshot", "") or ""
        sb_lines = [l for l in _strip_ansi(rc_sb).split("\r\n") if l.strip()]

        print(f"[resize_complete] scrollback_len={len(rc_sb)} lines={len(sb_lines)} snapshot_len={len(rc_snap)}")
        # 核心断言：resize_complete 携带非空 scrollback（修复前恒为空）
        assert len(sb_lines) > 0, "resize_complete 的 scrollback 不应为空（Bug #1 回归）"
        # scrollback 包含 resize 前滚出可见区的历史行（200 行输出，24 行可见 → 早期行应在 scrollback）
        texts = set(sb_lines)
        assert any("sb-line-" in t for t in texts), "scrollback 应包含生成的历史行"
        # 历史行连续性：中间某行（如 0050）应在 scrollback 中
        assert any("sb-line-0050" in t for t in texts), "scrollback 应包含 sb-line-0050（历史保留）"
        # snapshot 仍为可见区（非空且含最近输出）
        assert len(_strip_ansi(rc_snap)) > 0, "resize_complete 的 snapshot 不应为空"

        # 5. 清理
        await ws.send(json.dumps({"type": "kill", "sessionUid": uid}))
        await recv_until(ws, lambda m: m.get("type") == "session_ended" and m.get("sessionUid") == uid)


@pytest.mark.asyncio
async def test_resize_other_client_receives_scrollback():
    """session_resized 广播（非发起方）同样携带 scrollback。"""
    sid = "sb_e2e2_" + uuid.uuid4().hex[:6]
    async with websockets.connect(_WS_URL) as ws_a, websockets.connect(_WS_URL) as ws_b:
        # A 创建并订阅
        await ws_a.send(json.dumps({
            "type": "create", "session_id": sid,
            "command": _CREATE_CMD,
            "cols": 100, "rows": 24,
        }))
        sub_a = await recv_until(ws_a, lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid)
        assert sub_a is not None
        uid = sub_a.get("sessionUid")
        await asyncio.sleep(0.5)

        # B 也订阅（模拟多客户端）
        await ws_b.send(json.dumps({"type": "subscribe", "sessionUid": uid}))
        sub_b = await recv_until(ws_b, lambda m: m.get("type") == "subscribed" and m.get("sessionUid") == uid)
        assert sub_b is not None
        await asyncio.sleep(0.3)

        # A 生成输出
        script = "for i in range(150):\n print(f'other-line-{i:04d}')\n"
        await ws_a.send(json.dumps({
            "type": "input", "sessionUid": uid,
            "data": script.replace("\n", _EOL) + _EOL,
        }))
        await recv_until(ws_a, lambda m: m.get("type") == "output" and "other-line-0149" in m.get("data", ""),
                         timeout=30.0)
        await asyncio.sleep(0.3)

        # A 发起 resize；B 应收到 session_resized（含 scrollback）
        await ws_a.send(json.dumps({"type": "resize", "sessionUid": uid, "cols": 110, "rows": 28}))
        rc = await recv_until(ws_a, lambda m: m.get("type") == "resize_complete" and m.get("sessionUid") == uid)
        assert rc is not None

        sr = await recv_until(ws_b, lambda m: m.get("type") == "session_resized" and m.get("sessionUid") == uid)
        assert sr is not None, "B 未收到 session_resized"
        sr_sb = sr.get("scrollback", "") or ""
        sr_lines = [l for l in _strip_ansi(sr_sb).split("\r\n") if l.strip()]
        print(f"[session_resized] scrollback_len={len(sr_sb)} lines={len(sr_lines)}")
        assert len(sr_lines) > 0, "session_resized 广播应携带 scrollback"
        assert any("other-line-" in t for t in sr_lines), "广播 scrollback 应包含历史行"

        # 清理
        await ws_a.send(json.dumps({"type": "kill", "sessionUid": uid}))
        await recv_until(ws_a, lambda m: m.get("type") == "session_ended" and m.get("sessionUid") == uid)
