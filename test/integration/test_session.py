"""Session 错误处理集成测试

验证 Session 在子进程退出时是否正确捕获：
- exit_code
- error_message
- running 状态
"""

import sys
import time
import threading
import pytest

from src.session.session import Session


class TestSessionExitInfo:
    """测试 Session 退出信息捕获"""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        """每个测试后确保清理会话"""
        self._sessions = []
        yield
        for s in self._sessions:
            try:
                s.stop()
            except Exception:
                pass

    def _create_session(self, cmd, **kwargs):
        """创建并注册会话，便于清理"""
        # 使用时间戳生成唯一 ID
        sid = f"test-{time.time()}-{len(self._sessions)}"
        s = Session(sid, cmd, **kwargs)
        self._sessions.append(s)
        return s

    def test_normal_exit_zero(self):
        """进程正常退出 (exit=0) → exit_code=0, error_message=None"""
        s = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        s.start()
        # 等待进程退出
        self._wait_ended(s, timeout=5)
        assert s.exit_code == 0
        assert s.error_message is None

    def test_error_exit_42(self):
        """进程异常退出 (exit=42) → exit_code=42, error_message 非空"""
        s = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(42)"],
        )
        s.start()
        self._wait_ended(s, timeout=5)
        assert s.exit_code == 42
        assert s.error_message is not None
        assert "42" in s.error_message

    def test_error_exit_127(self):
        """进程异常退出 (exit=127) → 错误消息包含退出码"""
        s = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(127)"],
        )
        s.start()
        self._wait_ended(s, timeout=5)
        assert s.exit_code == 127
        assert s.error_message is not None
        assert "127" in s.error_message

    def test_session_running_false_after_exit(self):
        """进程退出后 session.running 变为 False"""
        s = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(1)"],
        )
        s.start()
        self._wait_ended(s, timeout=5)
        assert not s.running

    def test_exit_before_any_read(self):
        """进程在读取前就退出 → 仍能捕获退出码"""
        s = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(5)"],
        )
        s.start()
        self._wait_ended(s, timeout=5)
        assert s.exit_code == 5

    def test_exit_code_after_stop(self):
        """调用 stop() 时获取退出码"""
        s = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(9)"],
        )
        s.start()
        # 等待读者线程检测到 EOF
        self._wait_ended(s, timeout=5)
        s.stop()
        assert s.exit_code == 9

    def test_multiple_sessions_independent_exit_codes(self):
        """多个独立会话各自保有独立的 exit_code"""
        s1 = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(10)"],
        )
        s2 = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(20)"],
        )
        s1.start()
        s2.start()
        self._wait_ended(s1, timeout=5)
        self._wait_ended(s2, timeout=5)
        assert s1.exit_code == 10
        assert s2.exit_code == 20

    def test_long_running_process_still_running(self):
        """长时间运行中的进程 exit_code=None"""
        s = self._create_session(
            [sys.executable, "-c",
             "import time; print('ready'); time.sleep(30)"],
        )
        s.start()
        # 等待"ready"输出
        for _ in range(50):
            if s.output_offset > 0:
                break
            time.sleep(0.05)
        assert s.running
        assert s.exit_code is None
        s.stop()

    def test_no_error_message_for_normal_exit(self):
        """正常退出不会设置 error_message"""
        s = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        s.start()
        self._wait_ended(s, timeout=5)
        assert s.error_message is None

    def test_output_available_before_exit(self):
        """退出前产生的输出仍然可读"""
        s = self._create_session(
            [sys.executable, "-c",
             "print('hello from test'); import sys; sys.exit(1)"],
        )
        s.start()
        # 等待进程退出
        self._wait_ended(s, timeout=5)
        output = s.get_output()
        assert "hello from test" in output
        assert s.exit_code == 1

    def test_exit_code_not_none_after_script_completes(self):
        """脚本执行完毕后 exit_code 不为 None"""
        s = self._create_session(
            [sys.executable, "-c",
             "print('done'); import sys; sys.exit(0)"],
        )
        s.start()
        self._wait_ended(s, timeout=5)
        assert s.exit_code is not None

    # ---- 辅助方法 ----

    def _wait_ended(self, session, timeout: float = 5.0):
        """等待 session.running 变为 False"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not session.running:
                return
            time.sleep(0.05)
        # 超时后强制停止以防资源泄漏
        session.stop()
        pytest.fail(
            f"会话 '{session.id}' 在 {timeout}s 内未退出",
        )
