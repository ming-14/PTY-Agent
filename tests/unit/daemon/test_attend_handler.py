"""daemon/handlers/attend_handler.py 单元测试

用真实 socketpair 驱动 AttendHandler 长连接：
- 握手：attend_ready + attend_replay 帧
- 输入路由：input/key/mouse/resize → 会话方法调用
- 会话结束：notify_end → attend_ended + 连接关闭
- 分离/清理：detach 后退订、释放 hold
"""

import socket
import threading
import time

import pytest

from src.daemon.handlers.attend_handler import AttendHandler
from src.execution.context import HandlerContext
from src.protocol.envelope import unwrap as _env_unwrap
from src.protocol.message import Message
from src.session.publisher import SessionPublisher


class _Manager:
    def __init__(self, session):
        self._session = session

    def get_session(self, sid):
        return self._session if self._session.id == sid else None


class _MockSession:
    """满足 AttendHandler 访问的最小会话替身（真实 SessionPublisher + hold 计数）"""

    def __init__(self, sid="test", mode="pty", running=True):
        self.id = sid
        self.uid = "uid-test"
        self.running = running
        self.mode = mode
        self.exit_code = None
        self.error_message = None
        self.encoding = "utf-8"
        self._cols = 80
        self._rows = 24
        self.publisher = SessionPublisher()
        self._hold_count = 0
        self.inputs = []
        self.keys = []
        self.mice = []
        self.resizes = []
        self._mouse_tracking = False

    def acquire_hold(self):
        self._hold_count += 1

    def release_hold(self):
        self._hold_count -= 1

    @property
    def cols(self):
        return self._cols

    @property
    def rows(self):
        return self._rows

    @property
    def pty_type(self):
        return "test-pty"

    @property
    def output_offset(self):
        return 0

    def is_mouse_tracking(self):
        return self._mouse_tracking

    def mode_restore_seq(self):
        return ""

    def get_snapshot(self, keep_ansi=False):
        return "SNAPSHOT"

    def get_cursor_seq(self):
        return "\x1b[H"

    def resize(self, cols, rows):
        self.resizes.append((cols, rows))
        self._cols, self._rows = cols, rows
        return "SNAPSHOT"

    def write_input(self, data, pause_offsets=None):
        self.inputs.append(data)

    def key_input(self, key, mods=0):
        self.keys.append((key, mods))

    def key_up(self, key, mods=0):
        self.keys.append(("up", key, mods))

    def mouse_input(self, x, y, kind="press", button="left", mods=0):
        self.mice.append((x, y, kind, button, mods))


def _recv_body(sock, timeout=5.0):
    """读取一条响应并拆信封，带超时"""
    sock.settimeout(timeout)
    resp = Message.recv(sock)
    if resp is None:
        return None
    _, body, _ = _env_unwrap(resp)
    return body


def _start_handler(server_sock, session, msg=None):
    """在后台线程启动 AttendHandler.handle，返回会话管理器"""
    manager = _Manager(session)
    ctx = HandlerContext(manager)
    handler = AttendHandler()

    def _run():
        handler.handle(ctx, server_sock, msg or {"type": "attend", "id": session.id})

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return manager, t


def test_handshake_ready_and_replay():
    server, client = socket.socketpair()
    session = _MockSession()
    try:
        _, thread = _start_handler(server, session, {"type": "attend", "id": "test", "cols": 120, "rows": 40})

        ready = _recv_body(client)
        assert ready["type"] == "attend_ready"
        assert ready["sessionId"] == "test"
        assert ready["cols"] == 120  # 初始 resize 已生效
        assert ready["mouseTracking"] is False

        replay = _recv_body(client)
        assert replay["type"] == "attend_replay"
        assert "SNAPSHOT" in replay["text"]
        assert session.resizes == [(120, 40)]
    finally:
        client.close()
        server.close()


def test_input_routing():
    server, client = socket.socketpair()
    session = _MockSession()
    try:
        _, thread = _start_handler(server, session)
        _recv_body(client)  # ready
        _recv_body(client)  # replay

        Message.send(client, {"type": "attend_input", "data": "hello"})
        Message.send(client, {"type": "attend_key", "key": "Up", "mods": 2})
        Message.send(client, {"type": "attend_mouse", "x": 3, "y": 2, "kind": "press", "button": "left", "mods": 0})
        Message.send(client, {"type": "attend_resize", "cols": 100, "rows": 30})
        time.sleep(0.2)

        assert session.inputs == ["hello"]
        assert session.keys == [("Up", 2)]
        assert session.mice == [(3, 2, "press", "left", 0)]
        assert session.resizes == [(100, 30)]
    finally:
        client.close()
        server.close()


def test_ended_sends_ended_and_closes():
    server, client = socket.socketpair()
    session = _MockSession()
    try:
        _, thread = _start_handler(server, session)
        _recv_body(client)  # ready
        _recv_body(client)  # replay

        # 模拟会话自然结束
        session.running = False
        session.exit_code = 0
        session.publisher.notify_end(session)

        ended = _recv_body(client)
        assert ended["type"] == "attend_ended"
        assert ended["exitCode"] == 0
        # 连接被发送线程关闭 → recv 返回 None
        assert Message.recv(client) is None
        thread.join(timeout=2.0)
    finally:
        client.close()
        server.close()


def test_detach_cleans_hold_and_unsubscribe():
    server, client = socket.socketpair()
    session = _MockSession()
    try:
        _, thread = _start_handler(server, session)
        _recv_body(client)  # ready
        _recv_body(client)  # replay

        assert session._hold_count == 1
        Message.send(client, {"type": "attend_detach"})
        thread.join(timeout=2.0)

        assert session._hold_count == 0
        # 已退订：再 notify 不触发帧（连接已关）
        session.publisher.notify_subscribers(b"x", "stdout")
    finally:
        client.close()
        server.close()


def test_session_not_found_returns_error():
    server, client = socket.socketpair()
    try:
        ctx = HandlerContext(_Manager(_MockSession("other")))
        AttendHandler().handle(ctx, server, {"type": "attend", "id": "missing"})
        body = _recv_body(client)
        assert body["type"] == "error"
        assert "not found" in body["message"]
    finally:
        client.close()
        server.close()
