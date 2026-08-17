"""daemon/handlers/utils.py apply_client_defaults 单元测试

验证 --default terminal-size 对运行中会话的 resize 行为：
仅对运行中的 pty 会话、尺寸实际变化时调用 session.resize()；
子进程模式 / 未运行 / 尺寸未变 / 非法尺寸均静默跳过。
"""

from src.daemon.handlers.utils import _parse_terminal_size, apply_client_defaults


class _FakeSession:
    """带 cols/rows 属性与 resize 记录的假会话"""

    def __init__(self, cols=80, rows=24, mode="pty", running=True):
        self.id = "sid"
        self.client_config = {}
        self.mode = mode
        self.running = running
        self._cols = cols
        self._rows = rows
        self.resize_calls = []

    @property
    def cols(self):
        return self._cols

    @property
    def rows(self):
        return self._rows

    def resize(self, cols, rows):
        self.resize_calls.append((cols, rows))
        self._cols = cols
        self._rows = rows


class TestParseTerminalSize:
    def test_plain(self):
        assert _parse_terminal_size("120x40") == (120, 40)

    def test_multiply_sign(self):
        assert _parse_terminal_size("120×40") == (120, 40)

    def test_upper_x(self):
        assert _parse_terminal_size("120X40") == (120, 40)

    def test_invalid(self):
        assert _parse_terminal_size("abc") is None
        assert _parse_terminal_size("120x") is None
        assert _parse_terminal_size("0x40") is None
        assert _parse_terminal_size("-1x40") is None
        assert _parse_terminal_size(None) is None
        assert _parse_terminal_size(123) is None


class TestApplyResizeDefault:
    def test_resizes_running_pty(self):
        s = _FakeSession()
        apply_client_defaults(s, {"client_defaults": {"terminal_size": "120x40"}})
        assert s.resize_calls == [(120, 40)]
        assert s.client_config["terminal_size"] == "120x40"

    def test_skips_unchanged_size(self):
        s = _FakeSession(cols=120, rows=40)
        apply_client_defaults(s, {"client_defaults": {"terminal_size": "120x40"}})
        assert s.resize_calls == []

    def test_skips_subprocess(self):
        s = _FakeSession(mode="subprocess")
        apply_client_defaults(s, {"client_defaults": {"terminal_size": "120x40"}})
        assert s.resize_calls == []

    def test_skips_not_running(self):
        s = _FakeSession(running=False)
        apply_client_defaults(s, {"client_defaults": {"terminal_size": "120x40"}})
        assert s.resize_calls == []

    def test_skips_invalid_size(self):
        s = _FakeSession()
        apply_client_defaults(s, {"client_defaults": {"terminal_size": "abc"}})
        assert s.resize_calls == []
        # 配置仍记录（客户端已校验过；daemon 仅不触发 resize）
        assert s.client_config["terminal_size"] == "abc"

    def test_skips_missing_terminal_size(self):
        s = _FakeSession()
        apply_client_defaults(s, {"client_defaults": {"timeout": 30}})
        assert s.resize_calls == []
        assert s.client_config["timeout"] == 30

    def test_multiply_sign_resize(self):
        s = _FakeSession()
        apply_client_defaults(s, {"client_defaults": {"terminal_size": "100×30"}})
        assert s.resize_calls == [(100, 30)]

    def test_no_client_defaults(self):
        s = _FakeSession()
        apply_client_defaults(s, {})
        assert s.resize_calls == []
