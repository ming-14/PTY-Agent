"""session/process/info.py 单元测试"""

import os
import pytest

from src.process.info import (
    _signal_name,
    _format_exit_code_message,
    _format_pty_error,
    _get_process_detail,
    _get_process_tree,
)


class TestSignalName:
    def test_sigkill(self):
        result = _signal_name(9)
        assert "KILL" in result.upper() or "9" in result

    def test_sigterm(self):
        result = _signal_name(15)
        assert "TERM" in result.upper() or "15" in result

    def test_unknown_signal(self):
        result = _signal_name(99)
        assert "SIGUNKNOWN" in result or "99" in result


class TestFormatExitCodeMessage:
    def test_none_returns_none(self):
        assert _format_exit_code_message(None) is None

    def test_zero_returns_none(self):
        assert _format_exit_code_message(0) is None

    def test_negative_exit_code(self):
        result = _format_exit_code_message(-9)
        assert "信号" in result or "9" in result

    def test_positive_exit_code(self):
        result = _format_exit_code_message(1)
        assert "exit=1" in result or "异常" in result


class TestFormatPtyError:
    def test_string_exception(self):
        result = _format_pty_error(RuntimeError("test error"))
        assert "test error" in result

    def test_oserror_exception(self):
        err = OSError(2, "No such file")
        result = _format_pty_error(err)
        assert isinstance(result, str)
        assert len(result) > 0


class TestGetProcessDetail:
    def test_nonexistent_pid_returns_none(self):
        result = _get_process_detail(99999999)
        assert result is None

    def test_zero_pid_returns_none(self):
        result = _get_process_detail(0)
        assert result is None

    def test_negative_pid_returns_none(self):
        result = _get_process_detail(-1)
        assert result is None

    def test_current_process_returns_detail(self):
        pid = os.getpid()
        result = _get_process_detail(pid)
        assert result is not None
        assert result["pid"] == pid
        assert "name" in result
        assert "path" in result
        assert "commandLine" in result
        assert "ppid" in result
        assert isinstance(result["name"], str)
        assert len(result["name"]) > 0

    def test_current_process_has_ppid(self):
        pid = os.getpid()
        result = _get_process_detail(pid)
        assert result is not None
        assert isinstance(result["ppid"], int)
        assert result["ppid"] > 0


class TestGetProcessTree:
    def test_empty_pids_returns_empty(self):
        tree, details = _get_process_tree([])
        assert tree == []
        assert details == {}

    def test_single_pid_returns_tree(self):
        pid = os.getpid()
        tree, details = _get_process_tree([pid])
        assert len(tree) >= 1
        assert pid in details
        found = _find_pid_in_tree(tree, pid)
        assert found, f"PID {pid} not found in tree"

    def test_tree_structure_has_children(self):
        pid = os.getpid()
        tree, details = _get_process_tree([pid])
        assert len(tree) >= 1
        _assert_tree_structure(tree)

    def test_nonexistent_pid_in_tree(self):
        tree, details = _get_process_tree([99999999])
        assert len(tree) >= 1
        assert 99999999 in details

    def test_returns_details_dict(self):
        pid = os.getpid()
        tree, details = _get_process_tree([pid])
        assert isinstance(details, dict)
        assert pid in details
        assert "pid" in details[pid]
        assert "name" in details[pid]

    def test_includes_ancestor_processes(self):
        pid = os.getpid()
        tree, details = _get_process_tree([pid])
        assert len(details) > 1
        ppid = details[pid].get("ppid", 0)
        if ppid > 0:
            assert ppid in details


def _find_pid_in_tree(tree, pid):
    for node in tree:
        if node["pid"] == pid:
            return True
        if _find_pid_in_tree(node.get("children", []), pid):
            return True
    return False


def _assert_tree_structure(tree):
    for node in tree:
        assert "pid" in node
        assert "name" in node
        assert "children" in node
        assert isinstance(node["children"], list)
        _assert_tree_structure(node["children"])
