"""单实例检查集成测试

验证共享内存单实例检查在真实场景下的行为：
- 守护进程启动后共享内存包含 PID+端口
- 守护进程停止后共享内存被清理
- 僵死守护进程被正确检测和清理
- 不写磁盘文件
"""

import os
import sys
import time
import socket
import threading
import pytest

from src.session.shm_utils import (
    read_daemon_info_from_shm,
    read_port_from_shm,
    write_daemon_info_to_shm,
)
from src.daemon.lifecycle import (
    _pid_exists,
    _ping_daemon,
    _find_daemon_port,
    is_running,
)
from src.config import DATA_DIR, IS_WINDOWS


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows 共享内存集成测试")
class TestSingleInstanceIntegration:
    """单实例检查集成测试（Windows）"""

    def test_shm_contains_pid_and_port_after_write(self):
        shm = write_daemon_info_to_shm(os.getpid(), 54321)
        try:
            info = read_daemon_info_from_shm()
            assert info is not None
            pid, port = info
            assert pid == os.getpid()
            assert port == 54321
        finally:
            if shm:
                shm.close()

    def test_find_daemon_port_detects_zombie(self):
        shm = write_daemon_info_to_shm(99999999, 12345)
        try:
            result = _find_daemon_port()
            assert result is None
        finally:
            if shm:
                shm.close()

    def test_find_daemon_port_detects_alive_but_pingless(self):
        shm = write_daemon_info_to_shm(os.getpid(), 19999)
        try:
            result = _find_daemon_port()
            assert result is None
        finally:
            if shm:
                shm.close()

    def test_find_daemon_port_succeeds_with_real_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        from src.protocol.message import Message

        def handle():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            if msg and msg.get("type") == "ping":
                Message.send(conn, {"type": "pong"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        shm = write_daemon_info_to_shm(os.getpid(), port)
        try:
            result = _find_daemon_port()
            assert result == port
        finally:
            if shm:
                shm.close()
            srv.close()
            t.join(timeout=3)

    def test_no_pid_file_created(self):
        pid_file = os.path.join(DATA_DIR, "daemon.pid")
        assert not os.path.exists(pid_file)

    def test_no_data_dir_created(self):
        if IS_WINDOWS:
            assert not os.path.exists(DATA_DIR)

    def test_overwrite_shm_with_new_daemon(self):
        shm1 = write_daemon_info_to_shm(11111, 22222)
        shm1.close()
        shm2 = write_daemon_info_to_shm(33333, 44444)
        try:
            info = read_daemon_info_from_shm()
            assert info is not None
            pid, port = info
            assert pid == 33333
            assert port == 44444
        finally:
            if shm2:
                shm2.close()

    def test_is_running_false_when_no_daemon(self):
        shm = write_daemon_info_to_shm(99999999, 12345)
        try:
            assert is_running() is False
        finally:
            if shm:
                shm.close()
