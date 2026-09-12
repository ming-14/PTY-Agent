"""ProcessJob 单元测试

测试 Windows Job Object 的创建、分配、进程列表查询和清理功能。
仅在 Windows 平台运行，非 Windows 平台自动跳过。

重要：KILL_ON_JOB_CLOSE 是自动设置的，因此不要在 Job 中添加当前进程！
所有测试只将子进程分配到 Job 中。

分配一律用 `Popen._handle`（CreateProcess 亲手返回的句柄）+ `expected_pid`
校验，**不要** `OpenProcess(PROCESS_ALL_ACCESS, pid)`：子进程若已退出且 PID
被系统复用，就会把无关进程放进 kill-Job，关 Job 时误杀它（可能是别的用户程序、
甚至跑测试的宿主/CI runner）。
"""

import sys
import ctypes
import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Job Object 仅在 Windows 平台可用"),
]


@pytest.fixture
def job():
    """创建一个不分配任何进程的 ProcessJob 实例"""
    from src.backend.windows.job import ProcessJob
    j = ProcessJob(name="pytest-job")
    yield j
    j.close()


class TestProcessJobCreate:
    """ProcessJob 创建与关闭测试"""

    def test_create_and_close(self):
        """Job Object 创建后关闭不应异常"""
        from src.backend.windows.job import ProcessJob
        j = ProcessJob(name="test-create")
        assert j is not None
        j.close()
        j.close()  # 重复关闭应无害

    def test_create_with_name(self):
        """创建命名 Job Object"""
        from src.backend.windows.job import ProcessJob
        j = ProcessJob(name="test-named-job")
        assert j._hjob is not None
        assert j.name == "test-named-job"
        j.close()

    def test_context_manager(self):
        """上下文管理器应能正确创建和关闭"""
        from src.backend.windows.job import ProcessJob
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
        from src.backend.windows.job import ProcessJob

        j = ProcessJob(name="test-assign-subproc")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                ok = j.assign(proc._handle, expected_pid=proc.pid)
                assert ok, f"AssignProcessToJobObject 失败: err={ctypes.get_last_error()}"

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

    def test_assign_rejects_pid_mismatch(self):
        """expected_pid 与句柄实际归属不符 → 拒绝分配

        守的是最坏事故：子进程已退出、PID 被复用后，句柄指向无关进程；
        放进 KILL_ON_JOB_CLOSE 的 Job 再关句柄就会误杀那个进程。
        这里用"自己的子进程句柄 + 故意写错的 PID"触发校验分支。
        """
        import subprocess
        from src.backend.windows.job import ProcessJob

        j = ProcessJob(name="test-pid-mismatch")
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            assert j.assign(proc._handle, expected_pid=proc.pid + 1) is False
            # 关键：被拒绝的进程没有进 Job，因此关 Job 不会动它
            assert proc.pid not in j.query_process_list()
            # 对照：期望 PID 正确时能分配成功
            assert j.assign(proc._handle, expected_pid=proc.pid) is True
        finally:
            j.close()          # 这里只会杀掉我们自己放进 Job 的子进程
            proc.terminate()
            proc.wait()


class TestProcessJobSubprocess:
    """涉及子进程的 Job Object 测试"""

    def test_spawn_and_query(self):
        """启动子进程后可在 Job 进程列表中查到（独立 Job 实例）"""
        import subprocess
        from src.backend.windows.job import ProcessJob

        j = ProcessJob(name="test-spawn-query")
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            j.assign(proc._handle, expected_pid=proc.pid)
            pids = j.query_process_list()
            assert proc.pid in pids
        finally:
            proc.terminate()
            proc.wait()
            j.close()

    def test_kill_on_close(self):
        """KILL_ON_JOB_CLOSE：关闭 Job 后子进程应被终止"""
        import subprocess
        from src.backend.windows.job import ProcessJob

        j = ProcessJob(name="test-kill-on-close")
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            assert j.assign(proc._handle, expected_pid=proc.pid)
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


class TestProcessJobNotificationEvent:
    """通知到达事件（事件驱动消费）测试"""

    def test_wait_timeout_returns_false(self, job):
        """无通知时等待超时返回 False"""
        assert job.wait_notification(0.05) is False

    def test_push_sets_event_wait_returns_true(self, job):
        """推送通知后事件置位，wait 立即返回 True"""
        from src.backend.windows.job import JobNotification
        job._push_notif(JobNotification(msg_type=0, pid=100))
        assert job.wait_notification(0.1) is True

    def test_drain_clears_event(self, job):
        """drain 消费后事件清除，再次等待超时"""
        from src.backend.windows.job import JobNotification
        job._push_notif(JobNotification(msg_type=0, pid=100))
        items = job.drain_notifications()
        assert len(items) == 1
        assert job.wait_notification(0.05) is False

    def test_drain_returns_all_pushed(self, job):
        """drain 返回所有推送的通知"""
        from src.backend.windows.job import JobNotification
        job._push_notif(JobNotification(msg_type=0, pid=100))
        job._push_notif(JobNotification(msg_type=0, pid=200))
        items = job.drain_notifications()
        assert [n.pid for n in items] == [100, 200]

    def test_push_after_drain_sets_event_again(self, job):
        """drain 后再推送，事件重新置位（无丢失唤醒）"""
        from src.backend.windows.job import JobNotification
        job._push_notif(JobNotification(msg_type=0, pid=100))
        job.drain_notifications()
        job._push_notif(JobNotification(msg_type=0, pid=200))
        assert job.wait_notification(0.1) is True
        items = job.drain_notifications()
        assert [n.pid for n in items] == [200]


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
