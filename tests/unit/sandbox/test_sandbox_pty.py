"""src/sandbox/pty.py 单元测试（mock manager / wezterm Pty）"""

import pytest

import src.sandbox.pty as pty_mod
from src.sandbox.pty import SandboxPty

pytestmark = pytest.mark.skipif(not pty_mod._HAS_WEZTERM, reason="wezterm-py 不可用")


class _FakeTracker:
    def __init__(self):
        self.root = None
        self.manager = None

    def register_root(self, pid, hprocess=None):
        self.root = pid
        return True


class _FakeManager:
    def __init__(self, on_start=None, fail_start=False):
        self.started = False
        self.command = None
        self.on_start = on_start
        self.fail_start = fail_start

    def start(self):
        self.started = True
        if self.on_start is not None:
            self.on_start()

    def start_process(self, command_line, working_dir=None, env_vars=None, hpcon=None):
        self.command = command_line
        self.workdir = working_dir
        self.env_vars = env_vars
        self.hpcon = hpcon
        if self.fail_start:
            raise RuntimeError("start failed")
        return 1, 4242

    def get_exit_code(self):
        return 5


class _FakePty:
    """pywezterm.Pty 端口替身（沙箱场景：不 spawn，仅 ConPTY 创建/IO/尺寸控制）"""

    def __init__(self, cols=80, rows=24):
        self.cols = cols
        self.rows = rows
        self.writes = []
        self.closed = False
        self.resizes = []

    def hpcon(self):
        return 99

    def read(self, n=65536, timeout=None):
        return b"drained" if timeout == 0.0 else b"hello"

    def write(self, data):
        self.writes.append(data)

    def resize(self, cols, rows):
        self.resizes.append((cols, rows))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_conpty(monkeypatch):
    fake = _FakePty(80, 24)
    fake_module = type("FakePyWezterm", (),
                       {"Pty": lambda *a, **k: fake})()
    monkeypatch.setattr(pty_mod, "pywezterm", fake_module)
    return fake


class TestBuildCommandLine:
    def test_simple_command(self):
        # 基本拼接
        assert SandboxPty._build_command_line(["cmd.exe", "/c", "echo", "hi"], None, None) == \
            "cmd.exe /c echo hi"

    def test_param_with_special_chars_uses_windows_quoting(self):
        # 回归：shlex.quote（POSIX 单引号）会破坏 Windows 命令行（'&&' 作为
        # 字面量传给 cmd 而非命令分隔符），必须用 Windows 语义（list2cmdline）：
        # && 等 cmd 元字符原样保留，空格参数用双引号包裹
        cl = SandboxPty._build_command_line(
            ["cmd", "/c", "echo", "a", "&&", "echo", "b"], None, None)
        assert cl == "cmd /c echo a && echo b"

    def test_param_with_spaces_quoted(self):
        cl = SandboxPty._build_command_line(["cmd", "/c", "echo", "hello world"], None, None)
        assert cl == 'cmd /c echo "hello world"'

    def test_empty_command_raises(self):
        with pytest.raises(ValueError, match="command 不能为空"):
            SandboxPty._build_command_line([], None, None)


class TestSandboxPtyInit:
    def test_manager_required(self):
        with pytest.raises(ValueError, match="manager"):
            SandboxPty(["cmd.exe"], manager=None)

    def test_init_spawns_with_hpcon_and_registers_root(self, fake_conpty):
        mgr = _FakeManager()
        tracker = _FakeTracker()
        tracker.manager = mgr
        pty = SandboxPty(["cmd.exe", "/c", "echo", "hi"], 80, 24,
                         tracker=tracker, manager=mgr)
        assert mgr.started
        assert mgr.command == "cmd.exe /c echo hi"
        assert mgr.hpcon == 99  # ConPTY 句柄值透传 start_process
        assert tracker.root == 4242  # spawn 后立即登记根进程
        assert pty.get_child_pid() == 4242

    def test_init_cwd_passed_to_start_process(self, fake_conpty):
        # cwd 语义（Phase 16 无白名单）：透传 start_process working_dir
        mgr = _FakeManager()
        tracker = _FakeTracker()
        tracker.manager = mgr
        pty = SandboxPty(["cmd.exe"], 80, 24, cwd=r"C:\work",
                         tracker=tracker, manager=mgr)
        assert mgr.workdir == r"C:\work"

    def test_init_without_cwd_no_workdir(self, fake_conpty):
        mgr = _FakeManager()
        tracker = _FakeTracker()
        tracker.manager = mgr
        pty = SandboxPty(["cmd.exe"], 80, 24, cwd=None,
                         tracker=tracker, manager=mgr)
        assert mgr.workdir is None

    def test_start_failure_closes_conpty(self, fake_conpty):
        # 回归：start_process 失败时已创建的 ConPTY 句柄必须释放，避免泄漏
        mgr = _FakeManager(fail_start=True)
        with pytest.raises(RuntimeError, match="start failed"):
            SandboxPty(["cmd.exe"], 80, 24, manager=mgr)
        assert fake_conpty.closed


class TestSandboxPtyPort:
    def test_get_type(self, fake_conpty):
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        assert pty.get_type() == "win-sandbox"

    def test_read_and_drain(self, fake_conpty):
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        assert pty.read(1024) == b"hello"
        assert pty.drain(1024) == b"drained"

    def test_write_str_encoded_utf8(self, fake_conpty):
        # 回归：InputInterceptor 对 utf-8 编码透传 str，write 必须兜底编码为
        # bytes（与原生 ConPTY write 语义一致），否则 bytes(str) 抛 TypeError
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        pty.write("中文test")
        assert fake_conpty.writes == ["中文test".encode("utf-8")]

    def test_write_bytes_passthrough(self, fake_conpty):
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        pty.write(b"\xff\x00abc")
        assert fake_conpty.writes == [b"\xff\x00abc"]

    def test_fileno_none(self, fake_conpty):
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        assert pty.fileno() is None

    def test_inject_mouse_false(self, fake_conpty):
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        assert pty.inject_mouse_event(1, 1, 1, False) is False

    def test_resize_delegates_to_conpty(self, fake_conpty):
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        pty.resize(120, 40)
        assert fake_conpty.resizes == [(120, 40)]

    def test_close_closes_conpty_idempotent(self, fake_conpty):
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        pty.close()
        pty.close()
        assert pty._closed
        assert fake_conpty.closed

    def test_get_exit_code_delegates(self, fake_conpty):
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        assert pty.get_exit_code() == 5

    def test_read_after_close_empty(self, fake_conpty):
        pty = SandboxPty(["cmd.exe"], manager=_FakeManager())
        pty.close()
        assert pty.read(1024) == b""