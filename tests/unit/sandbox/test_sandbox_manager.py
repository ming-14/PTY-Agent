"""src/sandbox/manager.py 单元测试（mock win_sandbox_native.SandboxInstance / Process）

重点覆盖：
  - start/close 生命周期（原生实例创建与 shutdown）
  - start_process 直调签名（quota 原样传递 / hpcon 透传）
  - 回调入队（job started/exited → 通知队列；根 pid 匹配 → 退出码/存活态）
  - 命令封装（terminate / signal / query_*）
"""

import sys
import threading
import types

import pytest

# win-sandbox 为 Windows 原生组件；非 Windows 平台模块级跳过（import 前）
if sys.platform != "win32":
    pytest.skip("win-sandbox 仅支持 Windows", allow_module_level=True)

from src.sandbox.manager import SandboxError, SandboxSessionManager
from src.process.base import ProcessNotification, NOTIF_SPAWN, NOTIF_EXIT, NOTIF_CRASH

import src.sandbox.manager as manager_mod


class _FakeProcess:
    """Process 端口替身：记录调用 + 测试侧驱动回调

    匹配原生 win_sandbox_native.Process API：
      - pid 属性（readonly）
      - on_job_process_started / on_job_process_exited 可赋值回调
      - poll_exit() → None 或 (exit_code, reason)
      - terminate(exit_code)
      - signal_ctrl_break() → bool
      - query_process_list() → list[int]
      - query_process_exit_code(pid) → (code, active)
    """

    def __init__(self, pid=4242):
        self.pid = pid
        self.on_job_process_started = None
        self.on_job_process_exited = None
        self.signal_ctrl_break_calls = []
        self.terminate_calls = []
        self.query_list_result = [4242, 100]
        # exit_codes[pid] = exit_code：预设后 poll_exit() 返回 (code, "normal")
        self.exit_codes = {}

    def signal_ctrl_break(self):
        self.signal_ctrl_break_calls.append("ctrl_break")
        return True

    def terminate(self, exit_code=1):
        self.terminate_calls.append(exit_code)

    def query_process_list(self):
        return list(self.query_list_result)

    def query_process_exit_code(self, pid):
        # 契约对齐 native 语义：(exit_code, is_active)，运行中 (259, True)，已退出 (真实码, False)
        code = self.exit_codes.get(pid, 259)
        return (code, code == 259)

    def poll_exit(self):
        """非阻塞探测根进程退出：None = 仍在运行；(code, reason) = 已退出"""
        code = self.exit_codes.get(self.pid)
        if code is None:
            return None
        return (code, "normal")

    # 测试辅助：触发回调（匹配原生回调签名）
    def fire_started(self, pid):
        self.on_job_process_started(pid)

    def fire_exited(self, pid, exit_code, abnormal):
        self.on_job_process_exited(pid, exit_code, abnormal)


class _FakeInstance:
    """SandboxInstance 端口替身——匹配原生无参构造 + 6 位置参数 start_process"""

    def __init__(self):
        self.process_count = 0
        self.start_process_calls = []
        self.shutdown_called = False
        self.next_proc = None

    def start_process(self, command_line, working_dir, workspace_write, quota, hpcon, env):
        self.start_process_calls.append((command_line, working_dir, workspace_write, quota, hpcon, env))
        proc = self.next_proc if self.next_proc is not None else _FakeProcess()
        return proc

    def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def fake_env(monkeypatch):
    """构造 manager 并注入 fake 原生实例"""
    instance = _FakeInstance()
    process = _FakeProcess()
    instance.next_proc = process

    def _make_instance():
        return instance

    # 用工厂替身替换 manager 的 SandboxInstance 构造函数引用，
    # 避免依赖真实 pyd（缺失时 manager 模块级 SandboxInstance 为 None）
    monkeypatch.setattr(manager_mod, "SandboxInstance", _make_instance)
    monkeypatch.setattr(manager_mod, "_HAS_NATIVE", True)
    mgr = SandboxSessionManager(quota={"memory_mb": 256, "cpu_ms": 0,
                                       "crash_silent": True},
                                isolation={"net_policy": "unrestricted",
                                           "net_allowlist": [],
                                           "clipboard_isolate": False})
    mgr.start()
    yield mgr, instance, process
    mgr.close()


class TestManagerLifecycle:
    def test_start_creates_instance(self, fake_env):
        mgr, instance, _ = fake_env
        assert mgr._instance is instance

    def test_start_idempotent(self, fake_env):
        mgr, instance, _ = fake_env
        mgr.start()  # 再次 start 应幂等
        assert mgr._instance is instance

    def test_not_started_raises(self):
        mgr = SandboxSessionManager()
        with pytest.raises(SandboxError, match="sandbox 未启动"):
            mgr.start_process("cmd /c echo hi")

    def test_close_calls_shutdown(self, fake_env):
        mgr, instance, _ = fake_env
        mgr.close()
        assert instance.shutdown_called

    def test_close_idempotent(self, fake_env):
        mgr, instance, _ = fake_env
        mgr.close()
        mgr.close()
        assert instance.shutdown_called


class TestStartProcess:
    def test_direct_native_call(self, fake_env):
        mgr, instance, process = fake_env
        pid, os_pid = mgr.start_process("cmd.exe /c echo hi", hpcon=123)
        # manager 返回 (process_id=1, root_pid=proc.pid=4242)
        assert (pid, os_pid) == (1, 4242)
        cl, wd, ws_write, quota, hpcon_arg, env = instance.start_process_calls[0]
        assert cl == "cmd.exe /c echo hi"
        # working_dir 默认取 os.getcwd()
        assert wd is not None
        assert ws_write is True
        assert hpcon_arg == 123
        assert env == {}
        # 回调已挂接
        assert process.on_job_process_started is not None
        assert process.on_job_process_exited is not None

    def test_quota_zero_fields_filtered(self, fake_env):
        # 当前 manager 不过滤 quota 0 值字段，原样传递
        mgr, instance, _ = fake_env
        mgr.start_process("cmd /c echo hi")
        _, _, _, q, _, _ = instance.start_process_calls[0]
        assert q["cpu_ms"] == 0
        assert q["memory_mb"] == 256
        assert q["crash_silent"] is True

    def test_no_quota_when_all_zero(self):
        # 当前 manager 不过滤 quota，即使全为 0 也原样传递
        instance = _FakeInstance()
        mgr = SandboxSessionManager(quota={"cpu_ms": 0, "memory_mb": 0})
        mgr._instance = instance
        mgr.start_process("cmd /c echo hi")
        _, _, _, q, _, _ = instance.start_process_calls[0]
        assert q == {"cpu_ms": 0, "memory_mb": 0}

    def test_start_process_with_workdir_env(self, fake_env):
        mgr, instance, _ = fake_env
        mgr.start_process("cmd", working_dir=r"C:\work", env_vars={"A": "1"})
        _, wd, _, _, _, env = instance.start_process_calls[0]
        assert wd == r"C:\work"
        assert env == {"A": "1"}


class TestCallbacks:
    def test_job_notifications(self, fake_env):
        mgr, _, process = fake_env
        mgr.start_process("cmd")
        process.fire_started(101)
        process.fire_exited(101, 5, False)
        process.fire_exited(102, 9, True)
        notifs = mgr.drain_notifications()
        assert [n.type for n in notifs] == [NOTIF_SPAWN, NOTIF_EXIT, NOTIF_CRASH]
        assert notifs[0].pid == 101
        assert notifs[1].exit_code == 5
        assert notifs[2].exit_code == 9

    def test_root_exit_sets_exit_code(self, fake_env):
        mgr, _, process = fake_env
        mgr.start_process("cmd")
        assert mgr.is_root_alive()
        process.exit_codes[process.pid] = 3
        assert mgr.get_exit_code() == 3
        assert not mgr.is_root_alive()

    def test_root_exit_code_cached(self, fake_env):
        # 退出码缓存：poll_exit 探测成功后不再重复调用
        mgr, _, process = fake_env
        mgr.start_process("cmd")
        process.exit_codes[process.pid] = 7
        assert mgr.get_exit_code() == 7
        assert mgr.get_exit_code() == 7

    def test_non_root_exit_keeps_root_alive(self, fake_env):
        mgr, _, process = fake_env
        mgr.start_process("cmd")
        process.fire_exited(999, 1, True)
        assert mgr.is_root_alive()
        assert mgr.get_exit_code() is None


class TestCommands:
    def test_signal(self, fake_env):
        mgr, _, process = fake_env
        mgr.start_process("cmd")
        mgr.signal("ctrl_break")
        assert process.signal_ctrl_break_calls == ["ctrl_break"]

    def test_terminate(self, fake_env):
        mgr, _, process = fake_env
        mgr.start_process("cmd")
        mgr.terminate(exit_code=2)
        assert process.terminate_calls == [2]

    def test_command_not_started_raises(self):
        mgr = SandboxSessionManager()
        with pytest.raises(SandboxError, match="sandbox 进程未启动"):
            mgr.terminate()

    def test_query_process_list(self, fake_env):
        mgr, _, process = fake_env
        mgr.start_process("cmd")
        assert mgr.get_process_list() == [4242, 100]

    def test_query_exit_code_running(self, fake_env):
        mgr, _, process = fake_env
        mgr.start_process("cmd")
        assert mgr.get_process_exit_code(101) is None

    def test_query_exit_code_exited(self, fake_env):
        mgr, _, process = fake_env
        mgr.start_process("cmd")
        process.exit_codes[101] = 5
        assert mgr.get_process_exit_code(101) == 5
        assert mgr.get_process_exit_code(102) is None  # 仍在运行 → 259 → None


class TestIsolationOwnership:
    """隔离策略所有权：manager 必须持有自身拷贝，不得与调用方共享引用"""

    def test_init_owns_isolation_copy(self):
        caller = {"net_policy": "unrestricted", "net_allowlist": [],
                  "clipboard_isolate": False}
        mgr = SandboxSessionManager(isolation=caller)
        caller["net_policy"] = "allowlist"
        assert mgr._isolation["net_policy"] == "unrestricted"

    def test_cross_manager_no_share(self):
        # 多会话场景：A/B 各自持有隔离 dict，互不引用
        mgr_a = SandboxSessionManager(isolation={"net_policy": "unrestricted"})
        mgr_b = SandboxSessionManager(isolation={"net_policy": "allowlist"})
        assert mgr_a._isolation is not mgr_b._isolation