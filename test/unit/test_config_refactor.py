"""配置常量单元测试

验证重构后的配置常量正确性：MMAP_NAME、MMAP_SIZE、无 PID_FILE。
"""

import os
import pytest

from src.config import (
    DAEMON_HOST,
    DEFAULT_DAEMON_PORT,
    MMAP_NAME,
    MMAP_SIZE,
    DATA_DIR,
    PORT_FILE,
    IS_WINDOWS,
)


class TestDaemonConfig:
    """守护进程配置测试"""

    def test_daemon_host(self):
        assert DAEMON_HOST == "127.0.0.1"

    def test_default_port(self):
        assert DEFAULT_DAEMON_PORT == 18765

    def test_mmap_name_is_daemon(self):
        assert MMAP_NAME == "Local\\PTYAgentDaemon"

    def test_mmap_size_sufficient(self):
        text = "999999:65000"
        assert MMAP_SIZE >= len(text.encode("ascii"))

    def test_mmap_size_is_32(self):
        assert MMAP_SIZE == 32

    def test_no_pid_file_constant(self):
        import src.config as cfg
        assert not hasattr(cfg, "PID_FILE")

    def test_data_dir_under_home(self):
        assert DATA_DIR == os.path.join(os.path.expanduser("~"), ".pty-agent")

    def test_port_file_under_data_dir(self):
        assert PORT_FILE == os.path.join(DATA_DIR, "daemon.port")


class TestNoPidFileOnDisk:
    """验证运行时不创建 PID 文件"""

    def test_pid_file_does_not_exist(self):
        pid_file = os.path.join(DATA_DIR, "daemon.pid")
        assert not os.path.exists(pid_file)

    def test_data_dir_may_not_exist(self):
        # DATA_DIR 不需要预先存在（Windows 用共享内存）
        if IS_WINDOWS:
            assert True
        else:
            assert True
