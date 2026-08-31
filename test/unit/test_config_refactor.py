"""配置常量单元测试

验证重构后的配置常量正确性：无网络常量、无端口文件、共享内存常量存在。
"""

import os
import pytest

from src.config import (
    MMAP_DAEMON_INFO_NAME,
    MMAP_DAEMON_INFO_SIZE,
    MMAP_MAILBOX_NAME,
    MAILBOX_SLOT_COUNT,
    MAILBOX_SIZE,
    REQ_SHM_SIZE,
    RESP_SHM_SIZE,
    DATA_DIR,
    IS_WINDOWS,
)


class TestDaemonConfig:
    """守护进程配置测试"""

    def test_daemon_info_name(self):
        assert MMAP_DAEMON_INFO_NAME == "Local\\PTYAgentDaemon"

    def test_daemon_info_size_sufficient(self):
        # PID: 8 + state:1 + heartbeat: 20 = ~30
        assert MMAP_DAEMON_INFO_SIZE >= 48

    def test_mailbox_name(self):
        assert MMAP_MAILBOX_NAME == "Local\\PTYAgentMailbox"

    def test_mailbox_size(self):
        assert MAILBOX_SIZE == MAILBOX_SLOT_COUNT * 256
        assert MAILBOX_SLOT_COUNT == 32

    def test_req_shm_size(self):
        assert REQ_SHM_SIZE >= 256 * 1024

    def test_resp_shm_size(self):
        assert RESP_SHM_SIZE >= 64 * 1024 * 1024

    def test_no_socket_constants(self):
        import src.config as cfg
        assert not hasattr(cfg, "DAEMON_HOST")
        assert not hasattr(cfg, "DEFAULT_DAEMON_PORT")
        assert not hasattr(cfg, "PORT_FILE")
        assert not hasattr(cfg, "PING_TIMEOUT")
        assert not hasattr(cfg, "CONNECT_TIMEOUT")
        assert not hasattr(cfg, "SOCKET_LISTEN_BACKLOG")
        assert not hasattr(cfg, "SOCKET_RECV_BUFSIZE")

    def test_no_pid_file_constant(self):
        import src.config as cfg
        assert not hasattr(cfg, "PID_FILE")

    def test_data_dir_under_home(self):
        assert DATA_DIR == os.path.join(os.path.expanduser("~"), ".pty-agent")

    def test_no_port_file(self):
        port_file = os.path.join(DATA_DIR, "daemon.port")
        assert not os.path.exists(port_file)


class TestNoPidFileOnDisk:
    """验证运行时不创建 PID/端口文件"""

    def test_pid_file_does_not_exist(self):
        pid_file = os.path.join(DATA_DIR, "daemon.pid")
        assert not os.path.exists(pid_file)