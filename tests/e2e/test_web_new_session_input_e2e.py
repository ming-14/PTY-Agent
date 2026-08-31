# -*- coding: utf-8 -*-
"""E2E 测试：新建会话输入 + 重连恢复 scrollback（前端 bug 回归验证）。

验证（后端协议层，对应前端 bug）：
  1. 新建会话（create → subscribed）后发送 input 有回显（前端 Bug 1：
     乐观键迁移导致输入发往旧 sid 被丢弃——后端层面 input→output 链路必须完整）
  2. 会话产生 scrollback 后，断开重连再 subscribe（模拟 F5 刷新）：
     subscribed 响应必须携带 scrollback（含滚动出可见区的历史行）
     （前端 Bug 2：刷新后 scrollback 恢复依赖此响应）

daemon 由 web_daemon fixture 自动启动（未运行则启动、结束后停止），
不要求外部手动 `python -m src start`。

运行：
  python -m pytest tests/e2e/test_web_new_session_input_e2e.py -v
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


def _mk_sid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


async def _create_session(ws, sid, cols=100, rows=24):
    await ws.send(json.dumps({
        "type": "create", "session_id": sid,
        "command": _CREATE_CMD,
        "cols": cols, "rows": rows,
    }))
    sub = await recv_until(ws, lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid)
    assert sub is not None, f"create+subscribe 超时 sid={sid}"
    uid = sub.get("sessionUid") or sub.get("uid")
    assert uid, "subscribed 响应应携带 sessionUid"
    return uid


async def _kill_session(ws, uid):
    await ws.send(json.dumps({"type": "kill", "sessionUid": uid}))
    await recv_until(ws, lambda m: m.get("type") == "session_ended" and m.get("sessionUid") == uid)
    await asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_new_session_input_echo():
    """新建会话（create → subscribed）后 input 必须有回显（Bug 1 协议层）。"""
    sid = _mk_sid("input_e2e")
    async with websockets.connect(_WS_URL) as ws:
        uid = await _create_session(ws, sid)
        # 会话就绪后发送输入
        await asyncio.sleep(0.5)
        await ws.send(json.dumps({
            "type": "input", "sessionUid": uid,
            "data": "print('NEW_SESSION_INPUT_OK')\r\n",
        }))
        out = []
        got = await recv_until(
            ws,
            lambda m: m.get("type") == "output" and "NEW_SESSION_INPUT_OK" in m.get("data", ""),
            timeout=10.0, collect=out)
        assert got is not None, f"新建会话输入无回显；已收输出: {''.join(out)[-300:]!r}"
        await _kill_session(ws, uid)


@pytest.mark.asyncio
async def test_resubscribe_returns_scrollback():
    """会话产生 scrollback 后断开重连 subscribe：响应携带历史行（Bug 2 协议层）。"""
    sid = _mk_sid("sb_e2e")
    marker = f"SCROLL_MARK_{uuid.uuid4().hex[:6]}"
    uid = None
    ws1 = await websockets.connect(_WS_URL)
    try:
        # 小尺寸（40x5）便于滚动产生 scrollback
        uid = await _create_session(ws1, sid, cols=40, rows=5)
        await asyncio.sleep(0.5)
        # 打印 12 行（rows=5 → 至少 7 行滚入 scrollback）
        lines = "\\n".join(f"LINE_{i:02d}_{marker}" for i in range(12))
        await ws1.send(json.dumps({
            "type": "input", "sessionUid": uid,
            "data": f"print('{lines}')\r\n",
        }))
        # 等待输出全部到达
        out1 = []
        await recv_until(
            ws1,
            lambda m: m.get("type") == "output" and "LINE_11" in m.get("data", "") and marker in m.get("data", ""),
            timeout=10.0, collect=out1)
        await asyncio.sleep(0.5)
    finally:
        await ws1.close()

    # 模拟 F5 刷新：新连接 → list → subscribe
    async with websockets.connect(_WS_URL) as ws2:
        await ws2.send(json.dumps({"type": "list"}))
        lst = await recv_until(ws2, lambda m: m.get("type") == "session_list")
        assert lst is not None, "session_list 超时"
        sessions = lst.get("sessions", [])
        # 会话可能已归档（若窗口太小直接退出？不会——python -i 保持运行）
        s = next((x for x in sessions if x.get("id") == sid), None)
        if s is None:
            pytest.skip("会话未出现在 session_list（可能已退出）")
        real_uid = s.get("uid") or uid

        await ws2.send(json.dumps({"type": "subscribe", "sessionUid": real_uid}))
        sub = await recv_until(ws2, lambda m: m.get("type") == "subscribed" and (m.get("sessionUid") or m.get("uid")) == real_uid)
        assert sub is not None, "重连 subscribe 超时"
        scrollback = sub.get("scrollback") or ""
        # 关键断言 1：scrollback 必须包含早期行（LINE_00 在 rows=5 下早已滚出可见区）
        assert marker in scrollback, f"subscribed 响应 scrollback 缺失历史行；len={len(scrollback)} head={scrollback[:120]!r}"
        assert "LINE_00" in scrollback, f"scrollback 应包含最早期行 LINE_00；head={scrollback[:200]!r}"
        # 关键断言 2（Bug 2 回归）：scrollback 必须按 \r\n 分行——
        # 若后端返回 \n 分行，前端 split('\r\n') 会把整段当一行 → 折行错乱
        assert "\r\n" in scrollback, f"scrollback 未按 \\r\\n 分行（前端无法恢复）; head={scrollback[:200]!r}"

        # 清理
        await _kill_session(ws2, real_uid)
