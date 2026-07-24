"""ProcessJob 单元测试

测试 Windows Job Object 的创建、分配、进程列表查询和清理功能。
仅在 Windows 平台运行，非 Windows 平台自动跳过。

重要：KILL_ON_JOB_CLOSE 是自动设置的，因此不要在 Job 中添加当前进程！
所有测试只将子进程分配到 Job 中。
"""

import sys
import ctypes
import pytest
from typing import List

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Job Object 仅在 Windows 平台可用"),
]


@pytest.fixture
def job():
    """创建一个不分配任何进程的 ProcessJob 实例"""
    from src.pty.windows.job import ProcessJob
    j = ProcessJob(name="pytest-job")
    yield j
    j.close()


class TestProcessJobCreate:
    """ProcessJob 创建与关闭测试"""

    def test_create_and_close(self):
        """Job Object 创建后关闭不应异常"""
        from src.pty.windows.job import ProcessJob
        j = ProcessJob(name="test-create")
        assert j is not None
        j.close()
        j.close()  # 重复关闭应无害

    def test_create_with_name(self):
        """创建命名 Job Object"""
        from src.pty.windows.job import ProcessJob
        j = ProcessJob(name="test-named-job")
        assert j._hjob is not None
        assert j.name == "test-named-job"
        j.close()

    def test_context_manager(self):
        """上下文管理器应能正确创建和关闭"""
        from src.pty.windows.job import ProcessJob
        with ProcessJob(name="test-cm") as j:
            assert j._hjob is not None
        assert j._hjob is None


class TestProcessJobAssign:
    """进程分配到 Job 的测试"""

    def test_assign_invalid_handle(self, job):
        """分配 None 句柄应返回 False"""
        ok = job.assign(None)
        assert ok is False

    def test_query_empty_job(self, job):
        """未分配进程的 Job 应返回列表（可能为空或含特殊 PID）"""
        pids = job.query_process_list()
        assert isinstance(pids, list)

    def test_assign_and_query_subprocess(self):
        """分配子进程后可在 Job 进程列表中查到"""
        import subprocess
        from src.pty.windows.job import ProcessJob
        from src.pty.windows.convars import K, _CloseHandle

        j = ProcessJob(name="test-assign-subproc")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                PROCESS_ALL_ACCESS = 0x1F0FFF
                hproc = K.OpenProcess(PROCESS_ALL_ACCESS, False, proc.pid)
                assert hproc, f"OpenProcess 失败: err={ctypes.get_last_error()}"
                ok = j.assign(hproc)
                assert ok, f"AssignProcessToJobObject 失败: err={ctypes.get_last_error()}"
                _CloseHandle(hproc)

                pids = j.query_process_list()
                assert proc.pid in pids, f"PID {proc.pid} 不在列表中: {pids}"
            finally:
                proc.terminate()
                proc.wait()
        finally:
            j.close()

    def test_assign_zero_handle(self, job):
        """分配空句柄（0）应返回 False"""
        ok = job.assign(0)
        assert ok is False


class TestProcessJobSubprocess:
    """涉及子进程的 Job Object 测试"""

    def test_spawn_and_query(self):
        """启动子进程后可在 Job 进程列表中查到（独立 Job 实例）"""
        import subprocess
        from src.pty.windows.job import ProcessJob
        from src.pty.windows.convars import K, _CloseHandle

        j = ProcessJob(name="test-spawn-query")
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            PROCESS_ALL_ACCESS = 0x1F0FFF
            hproc = K.OpenProcess(PROCESS_ALL_ACCESS, False, proc.pid)
            if hproc:
                j.assign(hproc)
                _CloseHandle(hproc)
            pids = j.query_process_list()
            assert proc.pid in pids
        finally:
            proc.terminate()
            proc.wait()
            j.close()

    def test_kill_on_close(self):
        """KILL_ON_JOB_CLOSE：关闭 Job 后子进程应被终止"""
        import subprocess
        from src.pty.windows.job import ProcessJob
        from src.pty.windows.convars import K, _CloseHandle

        j = ProcessJob(name="test-kill-on-close")
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            PROCESS_ALL_ACCESS = 0x1F0FFF
            hproc = K.OpenProcess(PROCESS_ALL_ACCESS, False, proc.pid)
            if hproc:
                j.assign(hproc)
                _CloseHandle(hproc)
            j.close()  # 关闭 Job → 子进程应被终止
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


class TestProcessJobEdgeCases:
    """边界情况测试"""

    def test_unused_job_query(self, job):
        """未分配进程的 Job 查询进程列表应返回列表"""
        pids = job.query_process_list()
        assert isinstance(pids, list)

    def test_double_close_safe(self, job):
        """重复关闭 Job Object 应安全"""
        job.close()
        job.close()

    def test_query_after_close(self, job):
        """关闭后查询应返回空列表"""
        job.close()
        pids = job.query_process_list()
        assert pids == []

    def test_get_process_count(self, job):
        """get_process_count 应返回整数"""
        count = job.get_process_count()
        assert isinstance(count, int)
        assert count >= 0
