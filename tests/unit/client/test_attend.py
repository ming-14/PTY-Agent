"""client/attend.py 纯映射函数单元测试

覆盖：KEY_EVENT → 帧（可打印/特殊键/Ctrl+字母/Alt/Shift+大写/代理对/修饰键忽略）、
MOUSE_EVENT → 帧（点击差分/滚轮/拖拽）、latin-1 无损往返。
"""

import socket
import threading

import pytest

from src.client.attend import (
    MOD_ALT,
    MOD_CTRL,
    MOD_SHIFT,
    _AttendClient,
    decode_output_payload,
    map_key_event,
    map_mouse_event,
)
from src.protocol.envelope import response as _env_response
from src.protocol.message import Message


# ── map_key_event ────────────────────────────────────────────


def test_printable_char_as_text():
    frames = map_key_event(0x41, ord("a"), 0, {})
    assert frames == [{"type": "attend_input", "data": "a"}]


def test_uppercase_with_shift_to_lowercase_key():
    # 大写 + Shift → 小写 + 保留 SHIFT（对齐 web 前端）
    frames = map_key_event(0x41, ord("A"), 0x0010, {})
    assert frames == [{"type": "attend_key", "key": "a", "mods": MOD_SHIFT}]


def test_special_key():
    frames = map_key_event(0x26, 0, 0, {})  # VK_UP
    assert frames == [{"type": "attend_key", "key": "Up", "mods": 0}]


def test_function_key():
    frames = map_key_event(0x70, 0, 0, {})  # VK_F1
    assert frames == [{"type": "attend_key", "key": "F1", "mods": 0}]


def test_ctrl_letter():
    # Ctrl+C → 控制字符 0x03 → key='c' + CTRL
    frames = map_key_event(0x43, 0x03, 0x0008, {})
    assert frames == [{"type": "attend_key", "key": "c", "mods": MOD_CTRL}]


def test_alt_letter():
    frames = map_key_event(0x41, ord("a"), 0x0002, {})
    assert frames == [{"type": "attend_key", "key": "a", "mods": MOD_ALT}]


def test_chinese_char_as_text():
    frames = map_key_event(0x0, ord("中"), 0, {})
    assert frames == [{"type": "attend_input", "data": "中"}]


def test_surrogate_pair_as_text():
    # emoji 😀 = U+1F600，控制台以代理对分两次到达
    surrogate = {}
    frames = map_key_event(0x0, 0xD83D, 0, surrogate)  # 高代理
    assert frames == []
    frames = map_key_event(0x0, 0xDE00, 0, surrogate)  # 低代理
    assert frames == [{"type": "attend_input", "data": "\U0001F600"}]


def test_modifier_only_key_ignored():
    frames = map_key_event(0x11, 0, 0, {})  # VK_CONTROL 单独按下
    assert frames == []


def test_enter_special():
    frames = map_key_event(0x0D, 0x0D, 0, {})
    assert frames == [{"type": "attend_key", "key": "Enter", "mods": 0}]


# ── 真实控制台 uChar 为 str（WCHAR），须归一为 int 再比较 ──


def test_str_char_normalized():
    # 控制台 KEY_EVENT_RECORD.uChar 经 ctypes 读出是单字符 str
    frames = map_key_event(0x41, "a", 0, {})
    assert frames == [{"type": "attend_input", "data": "a"}]


def test_str_ctrl_letter_normalized():
    frames = map_key_event(0x43, "\x03", 0x0008, {})
    assert frames == [{"type": "attend_key", "key": "c", "mods": MOD_CTRL}]


def test_str_shift_uppercase_normalized():
    frames = map_key_event(0x41, "A", 0x0010, {})
    assert frames == [{"type": "attend_key", "key": "a", "mods": MOD_SHIFT}]


def test_str_surrogate_pair_normalized():
    surrogate = {}
    assert map_key_event(0x0, "\ud83d", 0, surrogate) == []  # 高代理
    frames = map_key_event(0x0, "\ude00", 0, surrogate)  # 低代理
    assert frames == [{"type": "attend_input", "data": "\U0001F600"}]


def test_detach_with_str_char():
    from src.client.attend import _is_detach, MOD_CTRL

    assert _is_detach(0xDC, "\\", MOD_CTRL) is True
    assert _is_detach(0xDC, "\x1c", MOD_CTRL) is True
    assert _is_detach(0x41, "a", MOD_CTRL) is False


# ── map_mouse_event ──────────────────────────────────────────


def test_mouse_press_release_diff():
    # 左键按下（prev 无 → 有）
    frames = map_mouse_event(5, 3, 0x0001, 0, 0, 0)
    assert frames == [
        {"type": "attend_mouse", "x": 5, "y": 3, "kind": "press", "button": "left", "mods": 0}
    ]
    # 左键释放（prev 有 → 无）
    frames = map_mouse_event(5, 3, 0, 0, 0, 0x0001)
    assert frames == [
        {"type": "attend_mouse", "x": 5, "y": 3, "kind": "release", "button": "left", "mods": 0}
    ]


def test_mouse_wheel_up():
    # dwButtonState 高字正增量 → wheel_up
    frames = map_mouse_event(5, 3, 120 << 16, 0, 0x0004, 0)
    assert frames == [
        {"type": "attend_mouse", "x": 5, "y": 3, "kind": "press", "button": "wheel_up", "mods": 0}
    ]


def test_mouse_wheel_down():
    frames = map_mouse_event(5, 3, ((-120) & 0xFFFF) << 16, 0, 0x0004, 0)
    assert frames[0]["button"] == "wheel_down"


def test_mouse_drag_move():
    frames = map_mouse_event(5, 3, 0x0001, 0, 0x0001, 0x0001)
    assert frames == [
        {"type": "attend_mouse", "x": 5, "y": 3, "kind": "move", "button": "left", "mods": 0}
    ]


def test_mouse_mods():
    frames = map_mouse_event(1, 1, 0x0002, 0x0010, 0, 0)  # 右键 + Shift
    assert frames[0]["mods"] == MOD_SHIFT


# ── 原始字节无损往返 ─────────────────────────────────────────


def test_output_payload_roundtrip():
    raw = bytes(range(256)) + b"\x00\xff\x1b[31m\xf0\x9f\x98\x80"
    text = raw.decode("latin-1")
    assert decode_output_payload(text) == raw


# ── 窗口操作序列剥离（CSI ... t 泄漏）───────────────────────


def test_strip_window_ops_removes_reply():
    from src.client.attend import _strip_window_ops, _strip_window_ops_text

    raw = b"hello\x1b[4;600;1200t\x1b[?1tworld"
    assert _strip_window_ops(raw) == b"helloworld"
    # 保留其他 CSI（SGR 颜色、光标定位等）
    assert _strip_window_ops(b"\x1b[31mred\x1b[1;1H") == b"\x1b[31mred\x1b[1;1H"
    # 文本版
    assert _strip_window_ops_text("a\x1b[4;600;1200tb") == "ab"


# ── 客户端帧循环（信封 + 字段名 + latin-1 与 daemon 侧一致）──


class _FakeState:
    def __init__(self):
        self._stop_event = threading.Event()

    def begin(self, mouse_tracking):
        pass

    def restore(self):
        pass

    def set_quick_edit(self, enabled):
        pass


def _send_frame(sock, frame: dict):
    sock.sendall(Message.encode(_env_response(frame["type"], frame)))


def test_client_frame_loop_renders(capsysbinary):
    server, client = socket.socketpair()
    ac = _AttendClient(_FakeClient(), "test")
    ac._sock = client
    ac._state = _FakeState()
    try:
        _send_frame(server, {"type": "attend_replay", "sessionId": "test", "text": "\x1b[HREPLAY", "subprocess": False})
        # 原始输出：latin-1 映射字节 → 客户端还原后原样写出
        raw = b"HELLO\x1b[31mRED\x00\xff"
        _send_frame(server, {"type": "attend_output", "sessionId": "test", "text": raw.decode("latin-1"), "stream": "stdout"})
        _send_frame(server, {"type": "attend_mouse_mode", "sessionId": "test", "tracking": True})
        _send_frame(server, {"type": "attend_ended", "sessionId": "test", "exitCode": 0, "errorMessage": None})
        ac._frame_loop()

        out = capsysbinary.readouterr().out
        assert b"HELLO" in out
        assert b"\x1b[31mRED\x00\xff" in out
        assert ac._ended_info["exitCode"] == 0
    finally:
        client.close()
        server.close()


class _FakeClient:
    _credential_provider = None
