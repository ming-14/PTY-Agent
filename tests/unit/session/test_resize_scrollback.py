"""Session.resize() scrollback 保留单元测试

验证（用 stub screen/pty，不依赖真实 ConPTY）：
- resize() 返回 (snapshot, scrollback) 元组
- scrollback 在 pty.resize 之前捕获（纯 reflow 历史）
- 模型 scrollback 在 repaint 等待后清除（防 repaint 冗余），返回副本不受影响
- _apply_program_resize 广播携带 scrollback
"""

import pytest

from src.session.output import OutputMixin


class _StubScreen:
    """TerminalScreen 最小替身：记录调用顺序。"""

    def __init__(self):
        self.feed_count = 0
        self.resize_called = None
        self.cleared = False
        self._sb = "line-001\r\nline-002\r\nline-003\r\n"
        self._snap = "snapshot-text"

    def resize(self, cols, rows):
        self.resize_called = (cols, rows)

    def capture_scrollback(self, keep_ansi=False):
        return self._sb

    def clear_scrollback(self):
        self.cleared = True

    def snapshot(self, keep_ansi=False, include_cursor=False):
        return self._snap

    def cursor_position(self):
        return (0, 0, True)


class _StubPty:
    def __init__(self):
        self.resize_called = None

    def resize(self, cols, rows):
        self.resize_called = (cols, rows)


class _StubInterceptor:
    def __init__(self):
        self.resize_called = None

    def resize(self, cols, rows):
        self.resize_called = (cols, rows)


class _StubSession(OutputMixin):
    """仅实现 resize 所需属性的会话替身。"""

    def __init__(self, mode="pty"):
        self.mode = mode
        self._cols = 100
        self._rows = 30
        self.id = "test"
        self._screen = _StubScreen()
        self._pty = _StubPty()
        self._input_interceptor = _StubInterceptor()
        self._publisher = None


def test_resize_returns_snapshot_and_scrollback():
    s = _StubSession()
    snapshot, scrollback = s.resize(120, 40)
    assert snapshot == "snapshot-text"
    assert scrollback == "line-001\r\nline-002\r\nline-003\r\n"


def test_resize_order_screen_capture_pty_no_clear():
    """调用顺序：screen.resize → capture（pty 之前）→ pty.resize → snapshot。
    不清除模型 scrollback：模型是权威历史（前端重建依赖它），
    ConPTY repaint 冗余行仅使 scrollback 略长，无害。"""
    s = _StubSession()
    s.resize(120, 40)
    assert s._screen.resize_called == (120, 40)
    assert s._pty.resize_called == (120, 40)
    assert s._input_interceptor.resize_called == (120, 40)
    # 模型 scrollback 保留（不清除）
    assert not s._screen.cleared


def test_resize_subprocess_raises():
    s = _StubSession(mode="subprocess")
    with pytest.raises(RuntimeError):
        s.resize(120, 40)


def test_resize_screen_failure_does_not_crash():
    """screen resize 失败时仍返回元组不崩溃（snapshot 读取仍可用）"""
    s = _StubSession()

    class _BrokenScreen(_StubScreen):
        def resize(self, cols, rows):
            raise RuntimeError("screen boom")

        def cursor_position(self):
            raise RuntimeError("no cursor")

    s._screen = _BrokenScreen()
    result = s.resize(120, 40)
    assert isinstance(result, tuple) and len(result) == 2
    # screen 失败时不捕获 scrollback（screen_ok=False），但 snapshot 仍返回
    assert result[1] == ""
    assert result[0] == "snapshot-text"


class _StubPublisher:
    def __init__(self):
        self.calls = []

    def notify_resized(self, session, cols, rows, snapshot="", scrollback=""):
        self.calls.append((cols, rows, snapshot, scrollback))


def test_program_resize_broadcasts_scrollback():
    s = _StubSession()
    s._publisher = _StubPublisher()
    s._apply_program_resize(120, 40)
    assert s._publisher.calls == [
        (120, 40, "snapshot-text", "line-001\r\nline-002\r\nline-003\r\n")
    ]


def test_program_resize_same_size_skips():
    s = _StubSession()
    s._publisher = _StubPublisher()
    s._apply_program_resize(100, 30)  # 与当前尺寸相同
    assert s._publisher.calls == []


def test_merge_scrollback_rows_merges_split_lines():
    """行尾空格行（reflow 变宽未合并的拆分残留）按文本规则合并。"""
    m = OutputMixin._merge_scrollback_rows
    # 32 列拆分的 dir 行：第一段 trimmed < 宽度且行尾空格 + 第二段行首空格 → 合并
    text = "2026/08/23  13:45    <DIR>      \r\n   __rikka_atomcode\r\nline-2\r\n"
    out = m(text, 113)
    assert out == "2026/08/23  13:45    <DIR>      __rikka_atomcode\r\nline-2\r\n"
    # 行尾非空格的正常行不合并
    text2 = "2026/08/20  23:47            14,691 _viminfo\r\nline-2\r\n"
    out2 = m(text2, 113)
    assert out2 == text2
    # 空输入
    assert m("", 113) == ""


def test_merge_scrollback_rows_ignores_ansi():
    """去 ANSI 后计算宽度，SGR 序列不参与合并判断。"""
    m = OutputMixin._merge_scrollback_rows
    text = "2026/08/23  13:45    <DIR>\x1b[0m      \r\n   __rikka_atomcode\r\n"
    out = m(text, 113)
    assert "__rikka_atomcode" in out and "\r\n   __rikka_atomcode" not in out
