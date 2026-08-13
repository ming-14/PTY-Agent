"""JobProcessTreeTracker 单元测试

测试 Windows Job Object 进程树追踪的创建、登记、进程列表查询、
kill_tree 终止和清理功能。仅在 Windows 平台运行，非 Windows 平台自动跳过。

重要：KILL_ON_JOB_CLOSE 是自动设置的，因此不要在 Job 中添加当前进程！
所有测试只将子进程分配到 Job 中。
"""

import sys
import ctypes
import subprocess
import pytest
from typing import List

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Job Object 仅在 Windows 平台可用"),
]


@pytest.fixture
def tracker():
    """创建一个不登记任何进程的 JobProcessTreeTracker 实例"""
    from src.process.windows.job_tracker import JobProcessTreeTracker
    t = JobProcessTreeTracker(name="pytest-job")
    yield t
    t.close()


def _spawn_sleep_seconds(seconds: int) -> subprocess.Popen:
    """启动一个 sleep 子进程"""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _open_process_handle(pid: int) -> int:
    """以全权限打开进程句柄"""
    from src.process.windows.api import _OpenProcess, PROCESS_ALL_ACCESS
    hproc = _OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    assert hproc, f"OpenProcess 失败: err={ctypes.get_last_error()}"
    return hproc


class TestJobProcessTreeTrackerCreate:
    """创建与关闭测试"""

    def test_create_and_close(self):
        """创建后关闭不应异常"""
        from src.process.windows.job_tracker import JobProcessTreeTracker
        t = JobProcessTreeTracker(name="test-create")
        assert t is not None
        t.close()
        t.close()  # 重复关闭应无害

    def test_create_with_name(self):
        """创建命名 Job Object"""
        from src.process.windows.job_tracker import JobProcessTreeTracker
        t = JobProcessTreeTracker(name="test-named-job")
        assert t._hjob is not None
        assert t.name == "test-named-job"
        t.close()


class TestRegisterRoot:
    """register_root 登记测试"""

    def test_register_invalid_handle(self, tracker):
        """登记 None 句柄应返回 False"""
        ok = tracker.register_root(12345, None)
        assert ok is False

    def test_register_zero_handle(self, tracker):
        """登记空句柄（0）应返回 False"""
        ok = tracker.register_root(12345, 0)
        assert ok is False

    def test_query_empty_job(self, tracker):
        """未登记进程的 Job 应返回列表"""
        pids = tracker.get_process_list()
        assert isinstance(pids, list)

    def test_register_and_query_subprocess(self):
        """登记子进程后可在 Job 进程列表中查到"""
        from src.process.windows.job_tracker import JobProcessTreeTracker
        from src.process.windows.api import _CloseHandle

        t = JobProcessTreeTracker(name="test-assign-subproc")
        try:
            proc = _spawn_sleep_seconds(5)
            try:
                hproc = _open_process_handle(proc.pid)
                ok = t.register_root(proc.pid, hproc)
                assert ok, f"AssignProcessToJobObject 失败: err={ctypes.get_last_error()}"
                _CloseHandle(hproc)

                pids = t.get_process_list()
                assert proc.pid in pids, f"PID {proc.pid} 不在列表中: {pids}"
            finally:
                proc.terminate()
                proc.wait()
        finally:
            t.close()


class TestJobSubprocess:
    """涉及子进程的测试"""

    def test_spawn_and_query(self):
        """启动子进程后可在 Job 进程列表中查到（独立 Job 实例）"""
        from src.process.windows.job_tracker import JobProcessTreeTracker
        from src.process.windows.api import _CloseHandle

        t = JobProcessTreeTracker(name="test-spawn-query")
        proc = _spawn_sleep_seconds(5)
        try:
            hproc = _open_process_handle(proc.pid)
            if hproc:
                t.register_root(proc.pid, hproc)
                _CloseHandle(hproc)
            pids = t.get_process_list()
            assert proc.pid in pids
        finally:
            proc.terminate()
            proc.wait()
            t.close()

    def test_kill_on_close(self):
        """KILL_ON_JOB_CLOSE：关闭 Job 后子进程应被终止"""
        from src.process.windows.job_tracker import JobProcessTreeTracker
        from src.process.windows.api import _CloseHandle

        t = JobProcessTreeTracker(name="test-kill-on-close")
        proc = _spawn_sleep_seconds(30)
        try:
            hproc = _open_process_handle(proc.pid)
            if hproc:
                t.register_root(proc.pid, hproc)
                _CloseHandle(hproc)
            t.close()  # 关闭 Job → 子进程应被终止
            proc.wait(timeout=5)
            assert proc.returncode is not None
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("KILL_ON_JOB_CLOSE 未在 5s 内终止子进程")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_kill_tree_terminates_subprocess(self):
        """kill_tree：枚举 + TerminateProcess 应终止所有进程"""
        from src.process.windows.job_tracker import JobProcessTreeTracker
        from src.process.windows.api import _CloseHandle

        t = JobProcessTreeTracker(name="test-kill-tree")
        proc = _spawn_sleep_seconds(30)
        try:
            hproc = _open_process_handle(proc.pid)
            if hproc:
                t.register_root(proc.pid, hproc)
                _CloseHandle(hproc)
            t.kill_tree(timeout=5.0)
            proc.wait(timeout=5)
            assert proc.returncode is not None
            # kill_tree 后 tracker 仍可查询（行为增强）
            assert proc.pid not in t.get_process_list()
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("kill_tree 未在 5s 内终止子进程")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            t.close()


class TestEdgeCases:
    """边界情况测试"""

    def test_unused_job_query(self, tracker):
        """未登记进程的 Job 查询进程列表应返回列表"""
        pids = tracker.get_process_list()
        assert isinstance(pids, list)

    def test_double_close_safe(self, tracker):
        """重复关闭 Job Object 应安全"""
        tracker.close()
        tracker.close()

    def test_query_after_close(self, tracker):
        """关闭后查询应返回空列表"""
        tracker.close()
        pids = tracker.get_process_list()
        assert pids == []

    def test_get_process_count(self, tracker):
        """get_process_count 应返回整数"""
        count = tracker.get_process_count()
        assert isinstance(count, int)
        assert count >= 0

    def test_is_root_alive_unregistered(self, tracker):
        """未登记 root 时 is_root_alive 应为 False"""
        assert tracker.is_root_alive() is False

    def test_get_root_exit_code_unregistered(self, tracker):
        """未登记 root 时 get_root_exit_code 应为 None"""
        assert tracker.get_root_exit_code() is None

    def test_kill_tree_unused(self, tracker):
        """未登记进程的 kill_tree 应无副作用"""
        tracker.kill_tree(timeout=1.0)
        assert tracker.get_process_list() == []

    def test_drain_notifications_empty(self, tracker):
        """未发生事件时 drain_notifications 返回空列表"""
        assert tracker.drain_notifications() == []

    def test_gui_windows_empty(self, tracker):
        """GUI 三件套默认返回空"""
        assert tracker.get_gui_windows() == []
        assert tracker.poll_gui_windows() == []
        assert tracker.close_gui_window(0) is False
