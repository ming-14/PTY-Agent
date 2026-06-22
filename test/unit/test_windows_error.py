"""Windows 错误码格式化单元测试

测试 translate_windows_error / format_process_exit_code / format_create_process_error
在各类错误码上的输出。

这些测试是跨平台的——所有映射基于静态符号名称表，不依赖 Windows API。
"""

import sys
import pytest

from src.pty.windows.error_msg import (
    translate_windows_error,
    format_process_exit_code,
    format_create_process_error,
    _NTSTATUS_NAMES,
    _WIN32_NAMES,
)


class TestTranslateWindowsError:
    """测试 translate_windows_error 函数"""

    def test_known_ntstatus_dll_not_found(self):
        """已知 NTSTATUS 0xC0000135 -> 包含 STATUS_DLL_NOT_FOUND"""
        msg = translate_windows_error(0xC0000135)
        assert "STATUS_DLL_NOT_FOUND" in msg
        assert "NTSTATUS" in msg

    def test_known_ntstatus_bad_image_format(self):
        """已知 NTSTATUS 0xC000007B -> 包含 STATUS_BAD_IMAGE_FORMAT"""
        msg = translate_windows_error(0xC000007B)
        assert "STATUS_BAD_IMAGE_FORMAT" in msg
        assert "NTSTATUS" in msg

    def test_known_win32_access_denied(self):
        """已知 Win32 错误码 5 -> ERROR_ACCESS_DENIED"""
        msg = translate_windows_error(5)
        assert "ERROR_ACCESS_DENIED" in msg

    def test_known_win32_elevation_required(self):
        """已知 Win32 错误码 740 -> ERROR_ELEVATION_REQUIRED"""
        msg = translate_windows_error(740)
        assert "ERROR_ELEVATION_REQUIRED" in msg

    def test_known_win32_file_not_found(self):
        """已知 Win32 错误码 2 -> ERROR_FILE_NOT_FOUND"""
        msg = translate_windows_error(2)
        assert "ERROR_FILE_NOT_FOUND" in msg

    def test_negated_input(self):
        """负数错误码（如进程退出码中的负值）通过无符号映射"""
        msg = translate_windows_error(-1073741515)
        assert "STATUS_DLL_NOT_FOUND" in msg

    def test_unknown_ntstatus(self):
        """未知 NTSTATUS -> 通用格式"""
        msg = translate_windows_error(0xF000ABCD)
        assert "NTSTATUS" in msg
        assert "0xF000ABCD" in msg

    def test_unknown_win32_error(self):
        """未知小错误码 -> 通用格式"""
        msg = translate_windows_error(99999)
        assert "error" in msg

    def test_zero(self):
        """退出码 0 -> 不触发错误映射"""
        msg = translate_windows_error(0)
        assert "NTSTATUS" not in msg
        assert "error" not in msg

    def test_all_ntstatus_codes_covered(self):
        """所有映射的 NTSTATUS 码都生成非空消息"""
        for code in _NTSTATUS_NAMES:
            msg = translate_windows_error(code)
            assert msg, f"NTSTATUS 0x{code:08X} 翻译结果为空"

    def test_all_win32_codes_covered(self):
        """所有映射的 Win32 码都生成非空消息"""
        for code in _WIN32_NAMES:
            msg = translate_windows_error(code)
            assert msg, f"Win32 错误码 {code} 翻译结果为空"


class TestFormatProcessExitCode:
    """测试 format_process_exit_code 函数"""

    def test_none(self):
        """None -> process still running"""
        assert format_process_exit_code(None) == "process still running"

    def test_exit_zero(self):
        """exit=0 -> process exited normally"""
        msg = format_process_exit_code(0)
        assert "process exited normally" in msg
        assert "(exit=0)" in msg

    def test_small_nonzero(self):
        """exit=42 -> 包含 exit=42 和错误描述"""
        msg = format_process_exit_code(42)
        assert "exit=42" in msg
        assert "error=" in msg

    def test_large_ntstatus(self):
        """NTSTATUS 退出码 -> 包含十六进制和名称"""
        msg = format_process_exit_code(0xC0000135)
        assert "0xC0000135" in msg
        assert "STATUS_DLL_NOT_FOUND" in msg

    def test_negative_ntstatus(self):
        """负数表示的 NTSTATUS"""
        msg = format_process_exit_code(-1073741515)
        assert "exit=" in msg
        assert "0xC0000135" in msg

    def test_still_active(self):
        """STILL_ACTIVE=259 -> 不命中特殊翻译"""
        from src.pty.windows.error_msg import STILL_ACTIVE
        msg = format_process_exit_code(STILL_ACTIVE)
        assert "process exited abnormally" in msg
        assert str(STILL_ACTIVE) in msg


class TestFormatCreateProcessError:
    """测试 format_create_process_error 函数"""

    def test_file_not_found(self):
        """错误码 2 -> 包含 create process failed 和 ERROR_FILE_NOT_FOUND"""
        msg = format_create_process_error(2)
        assert "create process failed" in msg
        assert "ERROR_FILE_NOT_FOUND" in msg

    def test_elevation_required(self):
        """错误码 740 -> 包含 ERROR_ELEVATION_REQUIRED"""
        msg = format_create_process_error(740)
        assert "create process failed" in msg
        assert "ERROR_ELEVATION_REQUIRED" in msg

    def test_unknown_error(self):
        """未知错误码 -> 回退通用格式"""
        msg = format_create_process_error(12345)
        assert "create process failed" in msg
        assert "error=12345" in msg
