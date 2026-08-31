# -*- coding: utf-8 -*-
"""E2E 测试：同名 sid 会话复用不污染（uid 主标识改造）。

验证：
  1. create 响应与 subscribe 响应携带 sessionUid
  2. 同名 sid 先后两个会话 uid 不同，操作互不串扰
  3. 旧协议（仅 session_id）经 resolve_sid 仍可操作（兼容）
  4. 历史归档保留同名 sid 的多条记录（uid 主键）

daemon 由 web_daemon fixture 自动启动（未运行则启动、结束后停止），
不要求外部手动 `python -m src start`。

运行：
  python -m pytest tests/e2e/test_web_uid_e2e.py -v
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


@pytest.mark.asyncio
async def test_same_sid_reuse_isolated():
    """同名 sid 先后两个会话：uid 不同、操作互不串扰、历史都保留。"""
    sid = "uid_e2e_" + uuid.uuid4().hex[:6]
    async with websockets.connect(_WS_URL) as ws:
        # 会话 A
        await ws.send(json.dumps({
            "type": "create", "session_id": sid,
            "command": _CREATE_CMD,
            "cols": 100, "rows": 24,
        }))
        sub_a = await recv_until(ws, lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid)
        assert sub_a is not None, "create+subscribe A 超时"
        uid_a = sub_a.get("sessionUid") or sub_a.get("uid")
        assert uid_a, "subscribed 响应应携带 sessionUid"

        # 通过 sessionUid 输入（新协议路径）
        await ws.send(json.dumps({
            "type": "input", "sessionUid": uid_a,
            "data": "print('MARK-A-ONLY')\r\n",
        }))
        out_a = []
        await recv_until(ws, lambda m: m.get("type") == "output" and "MARK-A-ONLY" in m.get("data", ""),
                         timeout=10.0, collect=out_a)
        assert any("MARK-A-ONLY" in d for d in out_a), "会话 A 应回显 MARK-A-ONLY"

        # 结束会话 A
        await ws.send(json.dumps({"type": "kill", "sessionUid": uid_a}))
        ended_a = await recv_until(ws, lambda m: m.get("type") == "session_ended" and m.get("sessionUid") == uid_a)
        assert ended_a is not None, "A 未收到 session_ended"
        await asyncio.sleep(0.5)

        # 同名会话 B（复用 sid）
        await ws.send(json.dumps({
            "type": "create", "session_id": sid,
            "command": _CREATE_CMD,
            "cols": 100, "rows": 24,
        }))
        sub_b = await recv_until(ws, lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid)
        assert sub_b is not None, "create+subscribe B 超时"
        uid_b = sub_b.get("sessionUid") or sub_b.get("uid")
        assert uid_b, "会话 B 应携带 sessionUid"
        assert uid_a != uid_b, "同名会话应分配不同 uid"

        # 旧协议路径（session_id）仍可操作会话 B
        await ws.send(json.dumps({
            "type": "input", "session_id": sid,
            "data": "print('MARK-B-ONLY')\r\n",
        }))
        out_b = []
        await recv_until(ws, lambda m: m.get("type") == "output" and "MARK-B-ONLY" in m.get("data", ""),
                         timeout=10.0, collect=out_b)
        assert any("MARK-B-ONLY" in d for d in out_b), "会话 B 应回显 MARK-B-ONLY"
        # A 的输出不应出现在 B 中
        assert not any("MARK-A-ONLY" in d for d in out_b), "会话 B 不应混入 A 的输出"

        # 清理 B
        await ws.send(json.dumps({"type": "kill", "sessionUid": uid_b}))
        await recv_until(ws, lambda m: m.get("type") == "session_ended" and m.get("sessionUid") == uid_b)
        await asyncio.sleep(0.5)

        # 历史应保留 A、B 两条记录（同名 sid 不再互相覆盖）
        await ws.send(json.dumps({"type": "history"}))
        hist = await recv_until(ws, lambda m: m.get("type") == "history_list")
        assert hist is not None, "history_list 超时"
        records = [s for s in hist.get("sessions", []) if s.get("id") == sid]
        uids_in_hist = {r.get("uid") for r in records if r.get("uid")}
        assert uid_a in uids_in_hist, f"历史应包含会话 A 的 uid={uid_a}，实际 {uids_in_hist}"
        assert uid_b in uids_in_hist, f"历史应包含会话 B 的 uid={uid_b}，实际 {uids_in_hist}"


@pytest.mark.asyncio
async def test_resize_via_session_uid():
    """通过 sessionUid 发送 resize 正常工作。"""
    sid = "uid_e2e_r_" + uuid.uuid4().hex[:6]
    async with websockets.connect(_WS_URL) as ws:
        await ws.send(json.dumps({
            "type": "create", "session_id": sid,
            "command": _CREATE_CMD,
            "cols": 100, "rows": 24,
        }))
        sub = await recv_until(ws, lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid)
        assert sub is not None
        uid = sub.get("sessionUid")
        await asyncio.sleep(0.5)

        await ws.send(json.dumps({
            "type": "resize", "sessionUid": uid, "cols": 120, "rows": 30,
        }))
        rc = await recv_until(ws, lambda m: m.get("type") == "resize_complete" and m.get("sessionUid") == uid)
        assert rc is not None, "resize_complete 超时"
        assert rc.get("cols") == 120 and rc.get("rows") == 30

        await ws.send(json.dumps({"type": "kill", "sessionUid": uid}))
        await recv_until(ws, lambda m: m.get("type") == "session_ended" and m.get("sessionUid") == uid)
