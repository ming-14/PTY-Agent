"""统一往返（protocol.request）健壮性单元测试

测试 roundtrip() 的共享内存请求/响应流程与各失败路径：
信箱满、超时、无响应、通道不可用；以及 controller.ensure_daemon。
使用 mock 替代共享内存（无 socket、无端口）。
"""

import pytest
from unittest.mock import patch, MagicMock

from src.protocol.request import roundtrip


def _fake_shm():
    shm = MagicMock()
    shm.size.return_value = 1024 * 1024
    return shm


class TestRoundtrip:
    """protocol.request.roundtrip 测试"""

    def test_success(self):
        with patch("src.protocol.request.read_auth_token",
                   return_value="tok"), \
             patch("src.protocol.request.open_shm") as mock_open, \
             patch("src.protocol.request.Mailbox") as mock_mailbox_cls, \
             patch("src.protocol.request.read_message",
                   return_value={"type": "pong"}):
            mock_open.side_effect = [_fake_shm(), _fake_shm()]
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = 0
            mock_mailbox.wait_done.return_value = True
            mock_mailbox_cls.return_value = mock_mailbox

            resp = roundtrip({"type": "ping"})
            assert resp == {"type": "pong"}
            # 令牌注入请求体
            assert mock_open.call_count == 2
            mock_mailbox.acquire_slot.assert_called_once()
            mock_mailbox.release_slot.assert_called_once()

    def test_mailbox_full(self):
        with patch("src.protocol.request.read_auth_token",
                   return_value="tok"), \
             patch("src.protocol.request.open_shm") as mock_open, \
             patch("src.protocol.request.Mailbox") as mock_mailbox_cls:
            mock_open.side_effect = [_fake_shm(), _fake_shm()]
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = None
            mock_mailbox_cls.return_value = mock_mailbox

            resp = roundtrip({"type": "ping"})
            assert resp["type"] == "error"
            assert "信箱已满" in resp["error"]

    def test_timeout(self):
        with patch("src.protocol.request.read_auth_token",
                   return_value="tok"), \
             patch("src.protocol.request.open_shm") as mock_open, \
             patch("src.protocol.request.Mailbox") as mock_mailbox_cls:
            mock_open.side_effect = [_fake_shm(), _fake_shm()]
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = 0
            mock_mailbox.wait_done.return_value = False
            mock_mailbox_cls.return_value = mock_mailbox

            resp = roundtrip({"type": "ping"})
            assert resp["type"] == "error"
            assert "超时" in resp["error"]

    def test_no_response_data(self):
        with patch("src.protocol.request.read_auth_token",
                   return_value="tok"), \
             patch("src.protocol.request.open_shm") as mock_open, \
             patch("src.protocol.request.Mailbox") as mock_mailbox_cls, \
             patch("src.protocol.request.read_message", return_value=None):
            mock_open.side_effect = [_fake_shm(), _fake_shm()]
            mock_mailbox = MagicMock()
            mock_mailbox.acquire_slot.return_value = 0
            mock_mailbox.wait_done.return_value = True
            mock_mailbox_cls.return_value = mock_mailbox

            resp = roundtrip({"type": "ping"})
            assert resp["type"] == "error"
            assert "读取响应失败" in resp["error"]

    def test_channel_unavailable(self):
        with patch("src.protocol.request.read_auth_token",
                   return_value="tok"), \
             patch("src.protocol.request.open_shm", return_value=None):
            resp = roundtrip({"type": "ping"})
            assert resp["type"] == "error"
            assert "通道" in resp["error"] or "共享内存" in resp["error"]


class TestEnsureDaemon:
    """controller.ensure_daemon 自动启动逻辑"""

    def _client_ensure(self):
        from src.client.controller import ensure_daemon
        return ensure_daemon

    def test_no_op_when_running(self):
        with patch("src.client.controller.is_running", return_value=True), \
             patch("src.client.controller.start_daemon") as mock_start:
            self._client_ensure()()
            mock_start.assert_not_called()

    def test_auto_starts_when_not_running(self):
        call_count = {"n": 0}

        def fake_running():
            call_count["n"] += 1
            return call_count["n"] >= 2

        with patch("src.client.controller.is_running",
                   side_effect=fake_running), \
             patch("src.client.controller.start_daemon") as mock_start, \
             patch("src.client.controller.time.sleep"):
            self._client_ensure()()
            mock_start.assert_called_once()

    def test_exits_when_start_fails(self):
        with patch("src.client.controller.is_running",
                   return_value=False), \
             patch("src.client.controller.start_daemon",
                   return_value=False), \
             patch("src.client.controller.time.sleep"):
            with pytest.raises(SystemExit):
                self._client_ensure()()


class TestNoLockFiles:
    """验证客户端不依赖 PID 文件/端口文件/锁文件"""

    def test_request_has_no_socket(self):
        import inspect
        import src.protocol.request as mod
        source = inspect.getsource(mod)
        assert "import socket" not in source

    def test_api_has_no_print_coupling(self):
        import src.client.api as mod
        assert "print_response" not in dir(mod)
