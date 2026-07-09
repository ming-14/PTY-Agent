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


class TestSessionStopInteractive:
    """Session.stop() 交互式会话测试

    回归测试：kill 卡死 bug。当子进程是交互式程序（如 python -i），
    reader 线程阻塞在 stdout.read() 等待输出。旧版 close() 先调用
    stdout.close()，与 reader 的 read() 争抢 BufferedReader 内部锁
    导致死锁，stop() 永不返回。

    修复后 close() 先 terminate 子进程，reader 收 EOF 自然退出，
    stop() 快速返回。
    """

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        self._sessions = []
        yield
        for s in self._sessions:
            try:
                s.stop(timeout=2)
            except Exception:
                pass

    def _create_session(self, cmd, **kwargs):
        sid = f"stop-test-{time.time()}-{len(self._sessions)}"
        s = Session(sid, cmd, **kwargs)
        self._sessions.append(s)
        return s

    def test_stop_interactive_python_no_deadlock(self):
        """stop() 交互式 python 会话不死锁"""
        s = self._create_session(
            [sys.executable, "-u", "-i"],
        )
        s.start()
        # 等待 python 就绪（输出 banner 和 >>>）
        self._wait_output(s, timeout=5)
        assert s.running

        # stop 必须不死锁
        self._stop_with_timeout(s, timeout=10)

    def test_stop_long_running_process(self):
        """stop() 长时间运行的进程"""
        s = self._create_session(
            [sys.executable, "-c", "import time; print('start', flush=True); time.sleep(120)"],
        )
        s.start()
        self._wait_output(s, timeout=10)
        assert s.running

        self._stop_with_timeout(s, timeout=10)

    def test_stop_with_blocked_reader(self):
        """stop() 时 reader 线程阻塞在 read() 中"""
        s = self._create_session(
            [sys.executable, "-u", "-i"],
        )
        s.start()
        # 确保 reader 线程已启动并阻塞在 read()
        time.sleep(1.0)
        assert s.running

        self._stop_with_timeout(s, timeout=10)

    def test_stop_sets_running_false(self):
        """stop() 后 running 变为 False"""
        s = self._create_session(
            [sys.executable, "-u", "-i"],
        )
        s.start()
        self._wait_output(s, timeout=5)
        assert s.running

        s.stop(timeout=10)
        assert not s.running

    def test_stop_idempotent(self):
        """多次 stop() 不抛异常"""
        s = self._create_session(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        s.start()
        self._wait_ended(s, timeout=5)
        s.stop()
        # 第二次 stop 不应抛异常
        s.stop()

    def test_stop_multiple_sessions(self):
        """连续 stop 多个交互式会话"""
        sessions = []
        for i in range(3):
            s = self._create_session(
                [sys.executable, "-u", "-i"],
            )
            s.start()
            self._wait_output(s, timeout=5)
            sessions.append(s)

        for s in sessions:
            self._stop_with_timeout(s, timeout=10)

    # ---- 辅助方法 ----

    def _wait_output(self, session, timeout=5.0):
        """等待会话产生输出"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if session.output_offset > 0:
                return
            time.sleep(0.05)
        pytest.fail(f"会话 '{session.id}' 在 {timeout}s 内无输出")

    def _wait_ended(self, session, timeout=5.0):
        """等待 session.running 变为 False"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not session.running:
                return
            time.sleep(0.05)
        session.stop()
        pytest.fail(f"会话 '{session.id}' 在 {timeout}s 内未退出")

    def _stop_with_timeout(self, session, timeout=10):
        """在子线程中调用 stop()，超时则判定死锁"""
        done = threading.Event()
        error = [None]

        def do_stop():
            try:
                session.stop(timeout=5)
            except Exception as e:
                error[0] = e
            finally:
                done.set()

        t = threading.Thread(target=do_stop, daemon=True)
        t.start()
        t.join(timeout=timeout)

        assert done.is_set(), \
            f"stop() 在 {timeout}s 内未完成——死锁"
        if error[0]:
            pytest.fail(f"stop() 抛出异常: {error[0]}")
