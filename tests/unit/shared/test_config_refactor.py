"""配置常量单元测试

验证配置常量正确性：固定监听端口、无 PID_FILE。
"""

import os

from src.config.common import DATA_DIR
from src.config.daemon import TOKEN_ENABLED, TOKEN_HOST, TOKEN_PORT, BASIC_ENABLED, TLS_ENABLED


class TestDaemonConfig:
    """守护进程配置测试"""

    def test_token_listener(self):
        assert TOKEN_HOST == "127.0.0.1"
        assert TOKEN_PORT == 10520

    def test_listener_enabled_flags(self):
        assert isinstance(TOKEN_ENABLED, bool)
        assert isinstance(BASIC_ENABLED, bool)
        assert isinstance(TLS_ENABLED, bool)

    def test_data_dir_under_home(self):
        assert DATA_DIR == os.path.join(os.path.expanduser("~"), ".pty-agent")

    def test_no_pid_file_constant(self):
        import src.config as cfg
        assert not hasattr(cfg, "PID_FILE")


class TestNoPidFileOnDisk:
    """验证运行时不创建 PID 文件"""

    def test_pid_file_does_not_exist(self):
        pid_file = os.path.join(DATA_DIR, "daemon.pid")
        assert not os.path.exists(pid_file)