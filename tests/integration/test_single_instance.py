"""单实例检查集成测试

固定端口模式验证：
- 无运行中守护进程时 is_running=False、端口/PID 发现返回 None
- 认证凭据共享内存与单实例锁相互独立
- 不写磁盘文件
"""

import os
import pytest

from src.ipc.shm import (
    generate_auth_token,
    write_auth_token,
)
from src.daemonctl import (
    _find_daemon_port,
    _find_daemon_pid,
    is_running,
)
from src.config.common import DATA_DIR, IS_WINDOWS


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows 单实例锁集成测试")
class TestSingleInstanceIntegration:
    """单实例检查集成测试（Windows）"""

    @pytest.fixture(autouse=True)
    def _no_running_daemon(self):
        """本类测试前提是"无运行中的守护进程"（互斥体未持有）；
        环境中有 daemon 运行时先停止，结束后再恢复启动。"""
        from src.daemonctl import is_running, start_daemon, stop_daemon
        was_running = is_running()
        if was_running:
            stop_daemon(force=True)
        yield
        if was_running:
            start_daemon()

    def test_not_running_by_default(self):
        assert is_running() is False

    def test_find_daemon_port_none_when_not_running(self):
        assert _find_daemon_port() is None

    def test_find_daemon_pid_none_when_not_running(self):
        assert _find_daemon_pid() is None

    def test_credentials_shm_independent_of_lock(self):
        """写入认证凭据共享内存不影响单实例锁状态"""
        shm = write_auth_token(generate_auth_token())
        try:
            assert is_running() is False
        finally:
            if shm:
                shm.close()

    def test_no_pid_file_created(self):
        pid_file = os.path.join(DATA_DIR, "daemon.pid")
        assert not os.path.exists(pid_file)

    def test_no_data_dir_created(self):
        """单实例检查不应创建数据目录（运行前后目录存在性不变）"""
        existed_before = os.path.exists(DATA_DIR)
        is_running()
        _find_daemon_pid()
        assert os.path.exists(DATA_DIR) == existed_before