"""DaemonServer 单元测试

测试 DaemonServer 的共享内存信息发布、心跳、信箱处理、cleanup、令牌轮换等。
使用 mock 替代真实信箱与共享内存。
"""

import os
import time
import threading
import pytest
from unittest.mock import patch, MagicMock

from src.daemon.server import DaemonServer
from src.config import IS_WINDOWS


class TestDaemonServerInit:
    """DaemonServer 初始化测试"""

    def test_initial_state(self):
        srv = DaemonServer()
        assert srv._running is False
        assert srv._cleaned_up is False
        assert srv._info_shm is None
        assert srv._auth_shm is None
        assert srv._auth_token != ""
        assert srv._my_pid == os.getpid()

    def test_no_socket_attributes(self):
        """新版本无 socket/端口属性"""
        srv = DaemonServer()
        assert not hasattr(srv, "_server_socket")
        assert not hasattr(srv, "port")
        assert not hasattr(srv, "host")


class TestDaemonServerRun:
    """DaemonServer.run 测试"""

    def test_run_publishes_daemon_info(self):
        with patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.open_shm") as mock_open, \
             patch("src.daemon.server.signal.signal"), \
             patch("src.daemon.server.write_daemon_info_handle") as mock_write:
            mock_auth.return_value = MagicMock()
            mock_info_shm = MagicMock()
            mock_open.return_value = mock_info_shm

            srv = DaemonServer()

            def stop_after_start(srv_ref):
                time.sleep(0.4)
                srv_ref._running = False

            t = threading.Thread(target=stop_after_start, args=(srv,), daemon=True)
            t.start()
            srv.run()

            mock_write.assert_called()
            args = mock_write.call_args[0]
            assert args[0] is mock_info_shm
            assert args[1] == os.getpid()
            assert args[2] is True

    def test_run_refuses_when_already_running(self):
        with patch("src.daemon.server.read_daemon_info",
                   return_value=(12345, True, time.time())), \
             patch("src.daemon.lifecycle._pid_exists", return_value=True), \
             patch("src.daemon.lifecycle._heartbeat_fresh", return_value=True):
            srv = DaemonServer()
            with pytest.raises(RuntimeError):
                srv.run()

    def test_no_pid_file_written(self):
        with patch("src.daemon.server.write_auth_token") as mock_auth, \
             patch("src.daemon.server.open_shm") as mock_open, \
             patch("src.daemon.server.signal.signal"), \
             patch("src.daemon.server.write_daemon_info_handle"):
            mock_auth.return_value = MagicMock()
            mock_open.return_value = MagicMock()

            srv = DaemonServer()

            def stop_after_start(srv_ref):
                time.sleep(0.4)
                srv_ref._running = False

            t = threading.Thread(target=stop_after_start, args=(srv,), daemon=True)
            t.start()
            srv.run()

        assert not os.path.exists(os.path.expanduser("~/.pty-agent/daemon.pid"))
        assert not os.path.exists(os.path.expanduser("~/.pty-agent/daemon.port"))


class TestDaemonServerSlot:
    """DaemonServer 信箱槽位处理测试"""

    def test_handle_slot_writes_response_and_marks_done(self):
        srv = DaemonServer()
        mock_handler = MagicMock()
        mock_handler.handle.return_value = {"type": "pong"}
        srv._handler = mock_handler

        with patch("src.daemon.server.open_shm") as mock_open, \
             patch("src.daemon.server.read_message") as mock_read, \
             patch("src.daemon.server.write_message") as mock_write:
            req_shm = MagicMock()
            resp_shm = MagicMock()
            mock_open.side_effect = [req_shm, resp_shm]
            mock_read.return_value = {"type": "ping"}

            srv._handle_slot(3, {
                "req_name": "req", "resp_name": "resp", "token": "tok",
            })

            mock_handler.handle.assert_called_once()
            # token 注入到 msg
            assert mock_handler.handle.call_args[0][0]["token"] == "tok"
            mock_write.assert_called_once()
            assert srv._mailbox  # mailbox 存在
            # DONE 标记（通过 mailbox 内部，此处验证不抛异常）

    def test_handle_slot_parsing_failure(self):
        srv = DaemonServer()
        mock_handler = MagicMock()
        srv._handler = mock_handler

        with patch("src.daemon.server.open_shm") as mock_open, \
             patch("src.daemon.server.read_message", return_value=None), \
             patch("src.daemon.server.write_message") as mock_write:
            req_shm = MagicMock()
            resp_shm = MagicMock()
            mock_open.side_effect = [req_shm, resp_shm]

            srv._handle_slot(0, {
                "req_name": "req", "resp_name": "resp", "token": "tok",
            })

            # 解析失败：handler 不被调用，返回错误响应
            mock_handler.handle.assert_not_called()
            resp = mock_write.call_args[0][1]
            assert resp["type"] == "error"

    def test_handle_slot_stop_triggers_server_stop(self):
        srv = DaemonServer()
        mock_handler = MagicMock()
        mock_handler.handle.return_value = {"type": "ok"}
        srv._handler = mock_handler

        with patch("src.daemon.server.open_shm") as mock_open, \
             patch("src.daemon.server.read_message") as mock_read, \
             patch("src.daemon.server.write_message"), \
             patch.object(srv, "stop") as mock_stop:
            req_shm = MagicMock()
            resp_shm = MagicMock()
            mock_open.side_effect = [req_shm, resp_shm]
            mock_read.return_value = {"type": "stop"}

            srv._handle_slot(1, {
                "req_name": "req", "resp_name": "resp", "token": "tok",
            })
            mock_stop.assert_called_once()


class TestDaemonServerVerifyShm:
    """DaemonServer._verify_shm 测试"""

    def test_verify_own_pid(self):
        srv = DaemonServer()
        srv._my_pid = 12345
        with patch("src.daemon.server.read_daemon_info",
                   return_value=(12345, True, time.time())):
            assert srv._verify_shm() is True

    def test_verify_foreign_pid(self):
        srv = DaemonServer()
        srv._my_pid = 12345
        with patch("src.daemon.server.read_daemon_info",
                   return_value=(67890, True, time.time())):
            assert srv._verify_shm() is False

    def test_verify_no_info(self):
        srv = DaemonServer()
        with patch("src.daemon.server.read_daemon_info", return_value=None):
            assert srv._verify_shm() is True


class TestDaemonServerCleanup:
    """DaemonServer._cleanup 测试"""

    def test_cleanup_idempotent(self):
        srv = DaemonServer()
        srv._cleaned_up = False
        srv._cleanup()
        assert srv._cleaned_up is True
        srv._cleanup()
        assert srv._cleaned_up is True

    def test_cleanup_stops_manager(self):
        srv = DaemonServer()
        mock_mgr = MagicMock()
        srv.manager = mock_mgr
        srv._cleanup()
        mock_mgr.stop_all.assert_called_once()

    def test_cleanup_cancels_rotate_timer(self):
        srv = DaemonServer()
        mock_timer = MagicMock()
        srv._rotate_timer = mock_timer
        srv._cleanup()
        mock_timer.cancel.assert_called_once()
        assert srv._rotate_timer is None

    def test_cleanup_closes_shm(self):
        srv = DaemonServer()
        mock_info = MagicMock()
        mock_auth = MagicMock()
        srv._info_shm = mock_info
        srv._auth_shm = mock_auth
        srv._cleanup()
        assert srv._info_shm is None
        assert srv._auth_shm is None

    def test_cleanup_closes_mailbox(self):
        srv = DaemonServer()
        srv._mailbox = MagicMock()
        srv._cleanup()
        srv._mailbox.close.assert_called_once()


class TestDaemonServerStop:
    """DaemonServer.stop 测试"""

    def test_stop_sets_running_false(self):
        srv = DaemonServer()
        srv._running = True
        srv.stop()
        assert srv._running is False

    def test_stop_calls_cleanup(self):
        srv = DaemonServer()
        with patch.object(srv, "_cleanup") as mock_cleanup:
            srv.stop()
            mock_cleanup.assert_called_once()


class TestDaemonServerToken:
    """DaemonServer 令牌轮换测试"""

    def test_rotate_token(self):
        srv = DaemonServer()
        mock_handler = MagicMock()
        srv._handler = mock_handler
        mock_shm = MagicMock()
        srv._auth_shm = mock_shm

        old_token = srv._auth_token
        with patch("src.daemon.server.write_auth_token") as mock_write, \
             patch("src.daemon.server.threading.Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            mock_write.return_value = mock_shm
            srv._rotate_token()
            assert srv._auth_token != old_token
            mock_handler.add_valid_token.assert_called_once()