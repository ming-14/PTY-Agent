"""SubprocessPseudoTerminal 退出码测试

验证 get_exit_code() 是否能正确捕获子进程的退出状态。
"""

import sys
import time
import pytest

from src.pty.subprocess import SubprocessPseudoTerminal


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
