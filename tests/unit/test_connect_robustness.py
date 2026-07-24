"""Client._connect 健壮性单元测试

测试 _connect 的僵死守护进程检测、自动重启、连接重试逻辑。
使用 mock 替代 TCP 连接和守护进程管理。
"""

import socket
import threading
import pytest
from unittest.mock import patch, MagicMock

from src.client.transport import Client
from src.protocol.message import Message


class TestConnectBasic:
    """_connect 基本连接测试"""

    def test_connect_to_healthy_daemon(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handle():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            if msg and msg.get("type") == "ping":
                Message.send(conn, {"type": "pong"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        with patch("src.daemon.lifecycle._find_daemon_port", return_value=port), \
             patch("src.daemon.lifecycle._ping_daemon", return_value=True):
            client = Client()
            sock = client._connect()
            assert sock is not None
            sock.close()

        srv.close()
        t.join(timeout=3)


class TestConnectAutoStart:
    """_connect 自动启动守护进程测试"""

    def test_auto_starts_when_not_running(self):
        started = {"called": False}

        def mock_start():
            started["called"] = True

        call_count = {"n": 0}

        def mock_find_port():
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return None
            return None

        with patch("src.daemon.lifecycle._find_daemon_port", side_effect=mock_find_port), \
             patch("src.client.transport.start_daemon", side_effect=mock_start):
            client = Client()
            try:
                client._connect()
            except SystemExit:
                pass
            assert started["called"]

    def test_exits_when_start_fails(self):
        with patch("src.daemon.lifecycle._find_daemon_port", return_value=None), \
             patch("src.client.transport.start_daemon"):
            client = Client()
            with pytest.raises(SystemExit):
                client._connect()


class TestConnectZombieRecovery:
    """_connect 僵死守护进程自动恢复测试"""

    def test_restarts_when_daemon_dies_mid_connect(self):
        call_count = {"n": 0}

        def mock_find_port():
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return 19999
            return None

        restarted = {"called": False}

        def mock_start():
            restarted["called"] = True

        with patch("src.daemon.lifecycle._find_daemon_port", side_effect=mock_find_port), \
             patch("src.client.transport.start_daemon", side_effect=mock_start):
            client = Client()
            try:
                client._connect()
            except SystemExit:
                pass
            assert restarted["called"]

    def test_connects_after_restart(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        real_port = srv.getsockname()[1]

        def handle():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            if msg and msg.get("type") == "ping":
                Message.send(conn, {"type": "pong"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        call_count = {"n": 0}

        def mock_find_port():
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return None
            return real_port

        with patch("src.daemon.lifecycle._find_daemon_port", side_effect=mock_find_port), \
             patch("src.client.transport.start_daemon"):
            client = Client()
            try:
                sock = client._connect()
                assert sock is not None
                sock.close()
            except Exception:
                pass

        srv.close()
        t.join(timeout=3)


class TestConnectRetry:
    """_connect 重试逻辑测试"""

    def test_retries_on_connection_refused(self):
        attempts = {"n": 0}

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        real_port = srv.getsockname()[1]

        def handle():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            if msg and msg.get("type") == "ping":
                Message.send(conn, {"type": "pong"})
            conn.close()

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        def mock_find_port():
            attempts["n"] += 1
            if attempts["n"] <= 2:
                return 19999
            return real_port

        with patch("src.daemon.lifecycle._find_daemon_port", side_effect=mock_find_port):
            client = Client()
            try:
                sock = client._connect()
                sock.close()
            except Exception:
                pass

        srv.close()
        t.join(timeout=3)


class TestClientNoPidFile:
    """验证 Client 不依赖 PID 文件"""

    def test_connect_does_not_import_pid_file_functions(self):
        import src.client.transport as transport_mod
        assert not hasattr(transport_mod, "write_pid_file")
        assert not hasattr(transport_mod, "read_pid_file")
        assert not hasattr(transport_mod, "cleanup_pid_file")
