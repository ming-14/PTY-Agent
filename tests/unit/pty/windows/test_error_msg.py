"""pty/windows/error_msg.py 单元测试"""

import sys
import pytest

from src.pty.windows.error_msg import (
    translate_windows_error,
    format_process_exit_code,
    format_create_process_error,
    STILL_ACTIVE,
)


class TestStillActive:
    def test_value(self):
        assert STILL_ACTIVE == 259


class TestTranslateWindowsError:
    def test_zero_returns_empty(self):
        assert translate_windows_error(0) == ""

    def test_ntstatus_access_violation(self):
        result = translate_windows_error(0xC0000005)
        assert "STATUS_ACCESS_VIOLATION" in result

    def test_ntstatus_dll_not_found(self):
        result = translate_windows_error(0xC0000135)
        assert "STATUS_DLL_NOT_FOUND" in result

    def test_win32_error_file_not_found(self):
        result = translate_windows_error(2)
        assert "ERROR_FILE_NOT_FOUND" in result

    def test_win32_error_access_denied(self):
        result = translate_windows_error(5)
        assert "ERROR_ACCESS_DENIED" in result

    def test_unknown_ntstatus(self):
        result = translate_windows_error(0xC0FFEE00)
        assert "NTSTATUS" in result

    def test_unknown_win32(self):
        result = translate_windows_error(99999)
        assert result == "" or "error" in result.lower() or "99999" in result


class TestFormatProcessExitCode:
    def test_none_returns_still_running(self):
        assert "running" in format_process_exit_code(None)

    def test_zero_returns_normal(self):
        result = format_process_exit_code(0)
        assert "normally" in result

    def test_nonzero_returns_abnormal(self):
        result = format_process_exit_code(1)
        assert "abnormally" in result

    def test_ntstatus_exit(self):
        result = format_process_exit_code(0xC0000005)
        assert "abnormally" in result
        assert "STATUS_ACCESS_VIOLATION" in result


class TestFormatCreateProcessError:
    def test_format(self):
        result = format_create_process_error(2)
        assert "create process failed" in result
        assert "ERROR_FILE_NOT_FOUND" in result
