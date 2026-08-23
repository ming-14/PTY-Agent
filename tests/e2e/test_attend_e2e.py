"""e2e 测试 —— attend 接管会话为完整实时终端

走真实 daemon + 真实 ConPTY 会话 + 真实 attend 协议：
1. exec 创建 cmd 会话（wezterm PTY）→ 会话保持运行
2. 新连接发 attend → 收 attend_ready / attend_replay（含当前屏幕真相）
3. 发送 echo 输入 → 原始输出流回显命令与结果
4. attend_detach 分离 → 会话仍可被 read/kill

依赖：wezterm-py 已编译（bin/pywezterm/pywezterm.pyd），否则 exec 创建
PTY 会话会失败，测试应跳过。
"""

import os
import re
import sys
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.client.transport import Client
from src.protocol.envelope import request as _env_request, unwrap as _env_unwrap
from src.protocol.message import Message

try:
    from src.pty.wezterm_pty import _HAS_WEZTERM
except Exception:  # pragma: no cover - 环境缺失时跳过
    _HAS_WEZTERM = False

pytestmark = pytest.mark.skipif(not _HAS_WEZTERM, reason="wezterm-py 不可用")

SID = "attend_e2e"


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", s)


@pytest.fixture()
def daemon():
    from src.daemonctl import is_running, start_daemon, stop_daemon

    started = False
    if not is_running():
        start_daemon()
        started = True
    yield
    if started:
        stop_daemon()


@pytest.fixture()
def client():
    return Client()


def _recv(sock, timeout=8.0):
    sock.settimeout(timeout)
    msg = Message.recv(sock)
    if msg is None:
        return None
    _, body, _ = _env_unwrap(msg)
    return body


def _send(sock, client, frame):
    msg = _env_request(frame["type"], frame)
    if client._credential_provider is not None:
        client._credential_provider.enrich(msg)
    Message.send(sock, msg)


def test_attend_full_flow(daemon, client):
    # 1. 创建真实 cmd 会话
    resp = client._send_recv(
        {"type": "exec", "id": SID, "command": "cmd", "trigger": r">", "timeout": 10}
    )
    assert resp.get("triggerReturnReason") in ("trigger_matched", "matched", "timeout")
    assert resp.get("program", {}).get("running") is True
    try:
        # 2. attend 握手 + replay
        sock = client._connect(autostart=False)
        sock.settimeout(10)
        try:
            _send(sock, client, {"type": "attend", "id": SID, "cols": 120, "rows": 30})
            seen = []
            replay_text = ""
            for _ in range(30):
                b = _recv(sock)
                assert b is not None, "attend 握手未收到帧"
                seen.append(b["type"])
                if b["type"] == "attend_replay":
                    replay_text = b.get("text", "")
                    break
            assert "attend_ready" in seen and "attend_replay" in seen
            # replay 应含 cmd 提示符（当前屏幕真相）
            assert ">" in _strip_ansi(replay_text)

            # 3. 输入回显
            _send(sock, client, {"type": "attend_input", "data": "echo HELLO_ATTEND\r"})
            got = b""
            deadline = time.time() + 5
            while time.time() < deadline:
                b = _recv(sock, timeout=2.0)
                if b is None:
                    break
                if b["type"] == "attend_output":
                    got += b.get("text", "").encode("latin-1")
                elif b["type"] == "attend_resync":
                    got += b.get("text", "").encode("utf-8", errors="ignore")
                if b"HELLO_ATTEND" in got:
                    break
            assert b"HELLO_ATTEND" in got, "echo 结果未出现在 attend 原始输出流"

            # 4. 分离
            _send(sock, client, {"type": "attend_detach"})
            time.sleep(0.5)
        finally:
            sock.close()

        # 5. 会话仍存活可读（attend 不影响其他消费者）
        r = client._send_recv({"type": "read", "id": SID, "timeout": 3}, autostart=False)
        assert ">" in r.get("outputStream", "")
    finally:
        client._send_recv({"type": "kill", "id": SID}, autostart=False)
