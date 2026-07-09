"""SubprocessPseudoTerminal 退出码测试 + 隐藏窗口测试

验证 get_exit_code() 是否能正确捕获子进程的退出状态，
以及 subprocess 模式下是否正确设置 CREATE_NO_WINDOW 和 STARTUPINFO 隐藏窗口。
"""

import sys
import subprocess
import time
import pytest

from src.pty.subprocess import SubprocessPseudoTerminal, _CREATE_NO_WINDOW, _STARTF_USESHOWWINDOW, _SW_HIDE


class TestSubprocessExitCode:
    """测试 SubprocessPseudoTerminal.get_exit_code()"""

    def test_normal_exit_zero(self):
        """进程正常退出 (exit=0) → get_exit_code() 返回 0"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        # 等待进程退出
        pty._proc.wait(timeout=5)
        code = pty.get_exit_code()
        assert code == 0
        pty.close()

    def test_error_exit_42(self):
        """进程异常退出 (exit=42) → 返回 42"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(42)"],
        )
        pty._proc.wait(timeout=5)
        code = pty.get_exit_code()
        assert code == 42
        pty.close()

    def test_exit_negative_one(self):
        """进程退出码为 -1 → 返回 -1（脚本 exit(-1) 在 Windows 上是 255）"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(-1)"],
        )
        pty._proc.wait(timeout=5)
        code = pty.get_exit_code()
        # exit(-1) → 255 (0xFF) on most platforms
        assert code is not None
        assert code == 255 or code != 0
        pty.close()

    def test_before_process_exits(self):
        """进程运行中时 get_exit_code() 返回 None"""
        # 启动一个长时间等待的进程
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )
        try:
            # 立即检查，进程应仍在运行
            code = pty.get_exit_code()
            assert code is None
        finally:
            pty.close()

    def test_after_close_returns_code(self):
        """调用 close() 后仍能获取退出码（已退出的进程）"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(7)"],
        )
        pty._proc.wait(timeout=5)
        pty.close()
        # close() 后退出码仍可获取
        code = pty.get_exit_code()
        assert code == 7

    def test_non_interactive_script_no_io(self):
        """不产生 I/O 的脚本（不读写 stdin/stdout）→ 退出码正确"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(127)"],
        )
        pty._proc.wait(timeout=5)
        code = pty.get_exit_code()
        assert code == 127
        pty.close()

    def test_rapid_exit_code_consistency(self):
        """快速连续获取 exit_code 保持稳定"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
        )
        pty._proc.wait(timeout=5)
        codes = []
        for _ in range(10):
            codes.append(pty.get_exit_code())
        # 应全部相等
        assert all(c == 3 for c in codes)
        assert all(c == codes[0] for c in codes)
        pty.close()

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="shell=True 在 Windows 上行为不同",
    )
    def test_shell_command_exit_code(self):
        """shell 模式下的命令退出码"""
        pty = SubprocessPseudoTerminal("exit 42", cols=80, rows=24)
        pty._proc.wait(timeout=5)
        code = pty.get_exit_code()
        assert code == 42
        pty.close()

    def test_no_side_effect_on_exit_before_io(self):
        """不读写的脚本：close 不会挂起"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        # 不调用 read/write，直接 close
        pty.close()
        # 不应抛出异常


class TestSubprocessNoWindow:
    """SubprocessPseudoTerminal 隐藏窗口标志测试

    验证所有 Popen 调用均设置了 CREATE_NO_WINDOW 和 STARTUPINFO(SW_HIDE)，
    确保控制台程序不会弹出可见窗口。
    """

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="CREATE_NO_WINDOW 仅 Windows 有效",
    )
    def test_list_command_popen_args(self, monkeypatch):
        """列表命令 Popen 传入 creationflags=CREATE_NO_WINDOW 和 startupinfo"""
        captured = {}
        original_popen = subprocess.Popen

        def mock_popen(*args, **kwargs):
            captured.update(kwargs)
            return original_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", mock_popen)
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        try:
            assert captured.get("creationflags") == _CREATE_NO_WINDOW
            si = captured.get("startupinfo")
            assert si is not None
            assert si.dwFlags & _STARTF_USESHOWWINDOW
            assert si.wShowWindow == _SW_HIDE
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="CREATE_NO_WINDOW 仅 Windows 有效",
    )
    def test_shell_cmd_popen_args(self, monkeypatch):
        """shell='cmd' (shell=True) Popen 传入 CREATE_NO_WINDOW 和 startupinfo"""
        captured = {}
        original_popen = subprocess.Popen

        def mock_popen(*args, **kwargs):
            captured.update(kwargs)
            return original_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", mock_popen)
        pty = SubprocessPseudoTerminal(
            "exit 0", cols=80, rows=24, shell="cmd",
        )
        try:
            assert captured.get("creationflags") == _CREATE_NO_WINDOW
            si = captured.get("startupinfo")
            assert si is not None
            assert si.dwFlags & _STARTF_USESHOWWINDOW
            assert si.wShowWindow == _SW_HIDE
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="CREATE_NO_WINDOW 仅 Windows 有效",
    )
    def test_shell_powershell_popen_args(self, monkeypatch):
        """shell='powershell' Popen 传入 CREATE_NO_WINDOW 和 startupinfo"""
        import shutil
        if not shutil.which("powershell"):
            pytest.skip("powershell.exe 不在 PATH 中")
        captured = {}
        original_popen = subprocess.Popen

        def mock_popen(*args, **kwargs):
            captured.update(kwargs)
            return original_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", mock_popen)
        pty = SubprocessPseudoTerminal(
            "exit 0", cols=80, rows=24, shell="powershell",
        )
        try:
            assert captured.get("creationflags") == _CREATE_NO_WINDOW
            si = captured.get("startupinfo")
            assert si is not None
            assert si.dwFlags & _STARTF_USESHOWWINDOW
            assert si.wShowWindow == _SW_HIDE
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="CREATE_NO_WINDOW 仅 Windows 有效",
    )
    def test_string_command_default_shell_popen_args(self, monkeypatch):
        """字符串命令默认 shell Popen 传入 CREATE_NO_WINDOW 和 startupinfo"""
        captured = {}
        original_popen = subprocess.Popen

        def mock_popen(*args, **kwargs):
            captured.update(kwargs)
            return original_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", mock_popen)
        pty = SubprocessPseudoTerminal(
            "exit 0", cols=80, rows=24,
        )
        try:
            assert captured.get("creationflags") == _CREATE_NO_WINDOW
            si = captured.get("startupinfo")
            assert si is not None
            assert si.dwFlags & _STARTF_USESHOWWINDOW
            assert si.wShowWindow == _SW_HIDE
        finally:
            pty.close()

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="CREATE_NO_WINDOW 仅 Windows 有效",
    )
    def test_no_visible_console_window(self):
        """启动控制台程序后不产生可见的控制台窗口"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import time; time.sleep(5)"],
        )
        try:
            gui_windows = pty.poll_gui_windows()
            assert len(gui_windows) == 0
        finally:
            pty.close()


class TestSubprocessCloseDeadlock:
    """SubprocessPseudoTerminal.close() 死锁回归测试

    回归测试：close() 曾先调用 stdout.close()，与 reader 线程中
    阻塞的 stdout.read() 争抢 BufferedReader 内部锁，导致死锁。
    修复后 close() 先 terminate 子进程（写端关闭→reader 收 EOF
    退出），再关闭读端。
    """

    def test_close_with_blocked_reader_no_deadlock(self):
        """reader 线程阻塞在 read() 时 close() 不死锁

        启动一个不产生输出的程序（等 stdin 输入），
        reader 线程会阻塞在 stdout.read()。此时调用 close()
        必须在合理时间内返回，不能死锁。
        """
        import threading

        # python -i 会输出 banner，不能用于测试"阻塞 read"。
        # 用 input() 阻塞但先不产生输出。
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "input()"],
        )
        try:
            # 启动 reader 线程模拟 Session 的后台读取
            read_done = threading.Event()

            def reader():
                try:
                    pty.read(65536)
                except Exception:
                    pass
                finally:
                    read_done.set()

            t = threading.Thread(target=reader, daemon=True)
            t.start()

            # 等待 reader 线程进入阻塞 read()
            time.sleep(0.5)
            assert not read_done.is_set(), "reader 不应在此之前退出"

            # close() 必须不死锁——设 10 秒超时检测
            close_done = threading.Event()

            def do_close():
                try:
                    pty.close()
                finally:
                    close_done.set()

            ct = threading.Thread(target=do_close, daemon=True)
            ct.start()
            ct.join(timeout=10)

            assert close_done.is_set(), "close() 在 10 秒内未完成——死锁"
        finally:
            try:
                pty.close()
            except Exception:
                pass

    def test_close_terminates_running_process(self):
        """close() 终止仍在运行的进程"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        # 进程应仍在运行
        assert pty._proc.poll() is None
        pty.close()
        # close 后进程应已终止
        assert pty._proc.poll() is not None

    def test_close_idempotent(self):
        """多次调用 close() 不抛异常"""
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        pty._proc.wait(timeout=5)
        pty.close()
        # 第二次 close 不应抛异常
        pty.close()

    def test_close_with_interactive_process(self):
        """close() 处理交互式进程（等 stdin 输入）不死锁

        模拟实际 kill 场景：python -i 启动后等输入，
        session.stop() → pty.close() 必须正常返回。
        """
        pty = SubprocessPseudoTerminal(
            [sys.executable, "-u", "-i"],
        )
        # 等待 python 就绪（输出版本信息）
        time.sleep(1.0)
        assert pty._proc.poll() is None, "python -i 应仍在运行"

        # close 必须快速完成
        import threading
        done = threading.Event()

        def do_close():
            try:
                pty.close()
            finally:
                done.set()

        t = threading.Thread(target=do_close, daemon=True)
        t.start()
        t.join(timeout=10)
        assert done.is_set(), "close() 交互式进程死锁"
