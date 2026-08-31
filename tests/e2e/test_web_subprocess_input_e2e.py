# -*- coding: utf-8 -*-
"""E2E 测试：子进程模式（mode="subprocess"）网页输入 → stdin。

验证（后端协议层 + 前端输入路由约定）：
  1. 创建子进程模式会话（create + mode="subprocess"）：
     subscribed 必须携带 mode="subprocess"、replay（stdout 尾部）。
  2. 订阅后发送 {type:'input'}：子进程从 stdin 读到并输出回显
     （逐行回显程序：for line in sys.stdin: print('ECHO:'+line)）。
     无回显是子进程自身行为，但输入必须能写入 stdin 并被子进程读到。
  3. 中文输入、分段输入（逐键无换行）同样能进 stdin。

前端侧（input.js）对应修改：子进程模式下按键不再发 {type:'key'}
（后端 KeyInputHandler 拒绝无终端编码），而是映射为 {type:'input'}
直接写入 stdin；doPaste 不再包裹 bracketed paste。

daemon 由 web_daemon fixture 自动启动（未运行则启动、结束后停止），
不要求外部手动 `python -m src start`。

运行：
  python -m pytest tests/e2e/test_web_subprocess_input_e2e.py -v
"""

import asyncio
import json
import time
import uuid

import pytest

websockets = pytest.importorskip("websockets", reason="需要 websockets 库")

_WS_URL = "ws://localhost:18766/ws"


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
                if collect is not None:
                    collect.append(m)
                if pred(m):
                    return m
        else:
            if collect is not None:
                collect.append(data)
            if pred(data):
                return data
    return None


def _mk_sid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


# 子进程模式逐行回显程序：stdin 每行 → stdout "ECHO:" + 行
_LINE_ECHO_CMD = (
    'python -u -c "'
    'import sys\n'
    'for line in sys.stdin:\n'
    '    sys.stdout.write(\'ECHO:\' + line)\n'
    '    sys.stdout.flush()\n'
    '"'
)


async def _create_subprocess(ws, sid):
    await ws.send(json.dumps({
        "type": "create",
        "session_id": sid,
        "command": _LINE_ECHO_CMD,
        "mode": "subprocess",
    }))
    sub = await recv_until(
        ws,
        lambda m: m.get("type") == "subscribed" and m.get("sessionId") == sid,
    )
    assert sub is not None, f"create+subscribe 超时 sid={sid}"
    assert sub.get("mode") == "subprocess", \
        f"subscribed 应携带 mode=subprocess: {sub.get('mode')}"
    uid = sub.get("sessionUid") or sub.get("uid")
    assert uid, "subscribed 响应应携带 sessionUid"
    return uid


async def _kill_session(ws, uid):
    await ws.send(json.dumps({"type": "kill", "sessionUid": uid}))
    await recv_until(
        ws,
        lambda m: m.get("type") == "session_ended" and m.get("sessionUid") == uid,
    )
    await asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_subprocess_input_echo():
    """子进程模式：{type:'input'} 必须写入 stdin 并被子进程读到（实时回显）。"""
    sid = _mk_sid("sub_input")
    marker = f"SUB_INPUT_{uuid.uuid4().hex[:6]}"
    async with websockets.connect(_WS_URL) as ws:
        uid = await _create_subprocess(ws, sid)
        await asyncio.sleep(0.5)
        # 发送一行输入（末尾 \n 提交行）
        await ws.send(json.dumps({
            "type": "input", "sessionUid": uid,
            "data": marker + "\n",
        }))
        got = await recv_until(
            ws,
            lambda m: m.get("type") == "output" and marker in m.get("data", ""),
            timeout=8.0,
        )
        assert got is not None, "子进程模式 input 未被子进程读取（stdin 未通）"
        assert got.get("data", "").startswith("ECHO:"), \
            f"回显应为 ECHO: 前缀: {got.get('data')!r}"
        await _kill_session(ws, uid)


@pytest.mark.asyncio
async def test_subprocess_input_unicode():
    """子进程模式：中文（非 BMP 外）输入同样写入 stdin。"""
    sid = _mk_sid("sub_uni")
    marker = f"中文输入_{uuid.uuid4().hex[:4]}"
    async with websockets.connect(_WS_URL) as ws:
        uid = await _create_subprocess(ws, sid)
        await asyncio.sleep(0.5)
        await ws.send(json.dumps({
            "type": "input", "sessionUid": uid,
            "data": marker + "\n",
        }))
        got = await recv_until(
            ws,
            lambda m: m.get("type") == "output" and marker in m.get("data", ""),
            timeout=8.0,
        )
        assert got is not None, "子进程模式中文 input 未被子进程读取"
        await _kill_session(ws, uid)


@pytest.mark.asyncio
async def test_subprocess_input_segmented():
    """子进程模式：逐键分段输入（无换行）累积后随 \n 提交。

    模拟前端 mapSubprocessKey 逐键发送（每键一个 {type:'input'}，
    不含 \n），子进程行缓冲需在收到 \n 后回显整行。
    """
    sid = _mk_sid("sub_seg")
    marker = f"SEGMENT_{uuid.uuid4().hex[:6]}"
    async with websockets.connect(_WS_URL) as ws:
        uid = await _create_subprocess(ws, sid)
        await asyncio.sleep(0.5)
        # 逐字符发送（模拟打字，无换行）
        for ch in marker:
            await ws.send(json.dumps({
                "type": "input", "sessionUid": uid, "data": ch,
            }))
            await asyncio.sleep(0.05)
        # 换行提交
        await ws.send(json.dumps({
            "type": "input", "sessionUid": uid, "data": "\n",
        }))
        got = await recv_until(
            ws,
            lambda m: m.get("type") == "output" and marker in m.get("data", ""),
            timeout=8.0,
        )
        assert got is not None, "分段逐键输入未被子进程读取"
        assert got.get("data", "").startswith("ECHO:" + marker), \
            f"分段输入回显应完整: {got.get('data')!r}"
        await _kill_session(ws, uid)
