"""单实例检查集成测试

验证共享内存单实例检查在真实场景下的行为：
- 守护进程信息区写入后能读取（PID+状态+心跳）
- 心跳过期检测
- 不写磁盘 PID/端口文件
"""

import os
import time
import pytest

from src.protocol.shm import (
    write_daemon_info_handle,
    read_daemon_info,
    cleanup_daemon_info,
)
from src.protocol.shm_utils import open_shm, close_shm
from src.daemon.lifecycle import (
    _heartbeat_fresh,
    is_running,
)
from src.config import (
    DATA_DIR, IS_WINDOWS,
    MMAP_DAEMON_INFO_NAME, MMAP_DAEMON_INFO_SIZE,
)


@pytest.fixture(autouse=True)
def _cleanup():
    cleanup_daemon_info()
    yield
    cleanup_daemon_info()


@pytest.fixture()
def _held_handle():
    """持有信息区句柄（与真实守护进程行为一致）"""
    shm = open_shm(MMAP_DAEMON_INFO_NAME, MMAP_DAEMON_INFO_SIZE)
    assert shm is not None
    yield shm
    close_shm(shm)


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows 共享内存集成测试")
class TestSingleInstanceIntegration:
    """单实例检查集成测试（Windows）"""

    def test_write_and_read_info(self, _held_handle):
        write_daemon_info_handle(_held_handle, os.getpid(), True, time.time())
        info = read_daemon_info()
        assert info is not None
        pid, running, heartbeat = info
        assert pid == os.getpid()
        assert running is True
        assert abs(time.time() - heartbeat) < 2

    def test_read_none_after_cleanup(self, _held_handle):
        write_daemon_info_handle(_held_handle, os.getpid(), True, time.time())
        cleanup_daemon_info()
        assert read_daemon_info() is None

    def test_is_running_true_when_healthy(self, _held_handle):
        write_daemon_info_handle(_held_handle, os.getpid(), True, time.time())
        assert is_running() is True

    def test_is_running_false_when_pid_dead(self, _held_handle):
        write_daemon_info_handle(_held_handle, 99999999, True, time.time())
        assert is_running() is False

    def test_is_running_false_when_stopped(self, _held_handle):
        write_daemon_info_handle(_held_handle, os.getpid(), False, time.time())
        assert is_running() is False

    def test_no_pid_file_created(self):
        pid_file = os.path.join(DATA_DIR, "daemon.pid")
        assert not os.path.exists(pid_file)

    def test_no_port_file_created(self):
        port_file = os.path.join(DATA_DIR, "daemon.port")
        assert not os.path.exists(port_file)

    def test_heartbeat_fresh_check(self):
        """心跳新鲜检查"""
        assert _heartbeat_fresh(time.time()) is True
        assert _heartbeat_fresh(time.time() - 60) is False

    def test_info_overwrite_with_new_daemon(self, _held_handle):
        write_daemon_info_handle(_held_handle, 11111, True, 1000.0)
        write_daemon_info_handle(_held_handle, 22222, True, 2000.0)
        info = read_daemon_info()
        assert info is not None
        assert info[0] == 22222


@pytest.mark.skipif(IS_WINDOWS, reason="Unix 文件 mmap 测试")
class TestSingleInstanceUnix:
    """单实例检查集成测试（Unix）"""

    def test_info_write_read(self, _held_handle):
        write_daemon_info_handle(_held_handle, os.getpid(), True, time.time())
        info = read_daemon_info()
        assert info is not None
        assert info[0] == os.getpid()
        assert info[1] is True