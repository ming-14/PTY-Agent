"""进程信息工具函数单元测试

测试 _get_process_name、_format_exit_code_message、_signal_name、_format_pty_error。
"""

import sys
import pytest

from src.session.process.info import (
    _get_process_name,
    _get_process_path,
    _format_exit_code_message,
    _signal_name,
    _format_pty_error,
)


class TestGetProcessName:
    """_get_process_name 测试"""

    def test_nonexistent_pid(self):
        """不存在的 PID 返回 'PID {pid}'"""
        name = _get_process_name(99999999)
        assert name == "PID 99999999"

    def test_current_pid(self):
        """当前进程 PID 返回进程名"""
        name = _get_process_name(0)
        assert isinstance(name, str)


class TestGetProcessPath:
    """_get_process_path 测试"""

    def test_nonexistent_pid(self):
        """不存在的 PID 返回 'PID {pid}'"""
        path = _get_process_path(99999999)
        assert path == "PID 99999999"

    def test_current_process(self):
        """当前进程可获取路径"""
        path = _get_process_path(0)
        assert isinstance(path, str)


class TestFormatExitCodeMessage:
    """_format_exit_code_message 测试"""

    def test_none_returns_none(self):
        """None 返回 None"""
        assert _format_exit_code_message(None) is None

    def test_zero_returns_none(self):
        """退出码 0 返回 None"""
        assert _format_exit_code_message(0) is None

    def test_nonzero_returns_message(self):
        """非零退出码返回消息"""
        msg = _format_exit_code_message(1)
        assert msg is not None
        assert isinstance(msg, str)

    def test_negative_on_unix(self):
        """Unix 负值（信号终止）"""
        if sys.platform == "win32":
            pytest.skip("Unix 信号测试")
        msg = _format_exit_code_message(-9)
        assert "信号" in msg or "SIGKILL" in msg or "9" in msg


class TestSignalName:
    """_signal_name 测试"""

    def test_sigkill(self):
        """SIGKILL=9"""
        name = _signal_name(9)
        assert "KILL" in name or "9" in name

    def test_sigterm(self):
        """SIGTERM=15"""
        name = _signal_name(15)
        assert "TERM" in name or "15" in name

    def test_unknown_signal(self):
        """未知信号"""
        name = _signal_name(999)
        assert "999" in name


class TestFormatPtyError:
    """_format_pty_error 测试"""

    def test_generic_exception(self):
        """通用异常返回字符串"""
        msg = _format_pty_error(RuntimeError("test error"))
        assert "test error" in msg

    def test_oserror_on_windows(self):
        """Windows OSError 格式化"""
        if sys.platform != "win32":
            pytest.skip("Windows 专用测试")
        err = OSError(2, "No such file")
        msg = _format_pty_error(err)
        assert isinstance(msg, str)