"""Client 共享内存通信健壮性单元测试

测试 _ensure_daemon 的自动启动逻辑、_send_recv 的共享内存请求/响应流程。
使用 mock 替代共享内存和守护进程管理（无 socket、无端口）。
"""

import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock

from src.client.transport import Client


class TestEnsureDaemon:
    """_ensure_daemon 自动启动测试"""

    def test_no_op_when_running(self):
        client = Client()
        with patch("src.client.transport.is_running", return_value=True), \
             patch("src.client.transport.start_daemon") as mock_start:
            client._ensure_daemon()
            mock_start.assert_not_called()

    def test_auto_starts_when_not_running(self):
        client = Client()
        call_count = {"n": 0}

        def mock_is_running():
            call_count["n"] += 1
            return call_count["n"] >= 2  # 第一次 False，之后 True

        with patch("src.client.transport.is_running", side_effect=mock_is_running), \
             patch("src.client.transport.start_daemon") as mock_start, \
             patch("src.client.transport.time.sleep"):
            client._ensure_daemon()
            mock_start.assert_called_once()

    def test_exits_when_start_fails(self):
        client = Client()
        with patch("src.client.transport.is_running", return_value=False), \
             patch("src.client.transport.start_daemon"), \
             patch("src.client.transport.time.sleep"):
            with pytest.raises(SystemExit):
                client._ensure_daemon()


class TestSendRecv:
    """_send_recv 共享内存请求/响应测试"""

    def _fake_shm(self):
        shm = MagicMock()
        shm.size.return_value = 1024 * 1024
        return shm

    def test_roundtrip(self):
        client = Client()
        with patch.object(client, "_ensure_daemon"), \
             patch("src.client.transport.open_shm") as mock_open, \
             patch("src.client.transport.read_auth_token", return_value="tok"), \
             patch("src.client.transport.Mailbox") as mock_mailbox_cls, \
             patch("src.client.transport.read_message",
                   return_value={"type": "pong"}):
            mock_open.side_effect = [self._fake_shm(), self._fake_shm()]

            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = 0
            mock_mailbox.wait_done.return_value = True
            mock_mailbox_cls.return_value = mock_mailbox

            resp = client._send_recv({"type": "ping"})
            assert resp == {"type": "pong"}
            # token 注入
            req = mock_open.call_args_list[0][0][0]
            assert "PTYAgentReq_" in req
            mock_mailbox.acquire_slot.assert_called_once()
            mock_mailbox.release_slot.assert_called_once()

    def test_mailbox_full(self):
        client = Client()
        with patch.object(client, "_ensure_daemon"), \
             patch("src.client.transport.open_shm") as mock_open, \
             patch("src.client.transport.read_auth_token", return_value="tok"), \
             patch("src.client.transport.Mailbox") as mock_mailbox_cls:
            mock_open.side_effect = [self._fake_shm(), self._fake_shm()]
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = None
            mock_mailbox_cls.return_value = mock_mailbox

            resp = client._send_recv({"type": "ping"})
            assert resp["type"] == "error"
            assert "信箱已满" in resp["error"]

    def test_timeout(self):
        client = Client()
        with patch.object(client, "_ensure_daemon"), \
             patch("src.client.transport.open_shm") as mock_open, \
             patch("src.client.transport.read_auth_token", return_value="tok"), \
             patch("src.client.transport.Mailbox") as mock_mailbox_cls:
            mock_open.side_effect = [self._fake_shm(), self._fake_shm()]
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = 0
            mock_mailbox.wait_done.return_value = False
            mock_mailbox_cls.return_value = mock_mailbox

            resp = client._send_recv({"type": "ping"})
            assert resp["type"] == "error"
            assert "超时" in resp["error"]

    def test_no_response_data(self):
        client = Client()
        with patch.object(client, "_ensure_daemon"), \
             patch("src.client.transport.open_shm") as mock_open, \
             patch("src.client.transport.read_auth_token", return_value="tok"), \
             patch("src.client.transport.Mailbox") as mock_mailbox_cls, \
             patch("src.client.transport.read_message", return_value=None):
            mock_open.side_effect = [self._fake_shm(), self._fake_shm()]
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = 0
            mock_mailbox.wait_done.return_value = True
            mock_mailbox_cls.return_value = mock_mailbox

            resp = client._send_recv({"type": "ping"})
            assert resp["type"] == "error"
            assert "读取响应失败" in resp["error"]


class TestClientNoLockFiles:
    """验证 Client 不依赖 PID 文件/端口文件/锁文件"""

    def test_transport_has_no_socket(self):
        import src.client.transport as transport_mod
        import inspect
        source = inspect.getsource(transport_mod)
        assert "import socket" not in source
        assert "socket.socket" not in source

    def test_transport_has_no_pid_file_functions(self):
        import src.client.transport as transport_mod
        assert not hasattr(transport_mod, "write_pid_file")
        assert not hasattr(transport_mod, "read_pid_file")
        assert not hasattr(transport_mod, "cleanup_pid_file")


class TestShellOperators:
    """_has_shell_operators 测试"""

    def test_detects_pipe(self):
        from src.client.transport import _has_shell_operators
        assert _has_shell_operators("echo a | grep b") is True

    def test_no_operators(self):
        from src.client.transport import _has_shell_operators
        assert _has_shell_operators("python -u -i") is False

    def test_quoted_operator_not_detected(self):
        from src.client.transport import _has_shell_operators
        assert _has_shell_operators('echo "a | b"') is False