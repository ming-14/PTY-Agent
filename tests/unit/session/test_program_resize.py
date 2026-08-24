"""程序尺寸变更（CSI 8;rows;colst）检测 + 广播 单元测试

不依赖真实 ConPTY：用 mock session 验证
- _detect_program_resize 正则解析 → 调用 _apply_program_resize
- _apply_program_resize 应用 resize + publisher.notify_resized 广播
- SessionPublisher 尺寸回调注册/通知（含 scrollback 参数）
"""

import pytest

from src.daemon.handlers.dispatcher import DaemonDispatcher  # noqa: F401  # 触发包导入
from src.session.publisher import SessionPublisher
from src.session.threads import _detect_program_resize, _PROGRAM_RESIZE_RE


# ── _PROGRAM_RESIZE_RE 正则 ──────────────────────────────────


def test_regex_matches_resize_seq():
    m = _PROGRAM_RESIZE_RE.search(b"abc\x1b[8;40;120tdef")
    assert m is not None
    assert (int(m.group(1)), int(m.group(2))) == (40, 120)  # rows, cols


def test_regex_ignores_other_csi():
    assert _PROGRAM_RESIZE_RE.search(b"\x1b[31mred\x1b[1;1H\x1b[?25l") is None
    assert _PROGRAM_RESIZE_RE.search(b"\x1b[4;600;1200t") is None  # 像素序列不误配


# ── _detect_program_resize 触发 ──────────────────────────────


class _FakeComp:
    def __init__(self, session):
        self._session = session
        self.session_id = "s1"

    def session_ref(self):
        return self._session


class _FakeSession:
    """满足 _apply_program_resize 的最小会话替身"""

    def __init__(self):
        self.id = "s1"
        self._cols = 80
        self._rows = 24
        self.publisher = SessionPublisher()
        self.applied = []
        self.notified = []

    def _apply_program_resize(self, cols, rows):
        self.applied.append((cols, rows))
        self._cols, self._rows = cols, rows
        self.publisher.notify_resized(self, cols, rows, "SNAP", "SB")


def test_detect_calls_apply():
    sess = _FakeSession()
    comp = _FakeComp(sess)
    _detect_program_resize(b"x\x1b[8;40;120ty", comp)
    assert sess.applied == [(120, 40)]


def test_detect_ignores_no_match():
    sess = _FakeSession()
    comp = _FakeComp(sess)
    _detect_program_resize(b"plain text", comp)
    assert sess.applied == []


# ── 广播（publisher 尺寸回调）───────────────────────────────


def test_notify_resized_invokes_callbacks_with_scrollback():
    pub = SessionPublisher()
    got = []
    pub.add_on_resized_callback(lambda s, c, r, snap, sb: got.append((c, r, snap, sb)))
    pub.notify_resized(None, 120, 40, "SNAP", "SB-LINES")
    assert got == [(120, 40, "SNAP", "SB-LINES")]


def test_notify_resized_scrollback_default_empty():
    """scrollback 缺省为空串（兼容未携带的调用方）"""
    pub = SessionPublisher()
    got = []
    pub.add_on_resized_callback(lambda s, c, r, snap, sb: got.append((c, r, snap, sb)))
    pub.notify_resized(None, 1, 2, "S")
    assert got == [(1, 2, "S", "")]


def test_remove_on_resized_callback():
    pub = SessionPublisher()
    got = []

    def cb(s, c, r, snap, sb):
        got.append((c, r))

    pub.add_on_resized_callback(cb)
    pub.notify_resized(None, 1, 2, "", "")
    pub.remove_on_resized_callback(cb)
    pub.notify_resized(None, 3, 4, "", "")
    assert got == [(1, 2)]
