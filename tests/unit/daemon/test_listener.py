"""Listener 单元测试

验证 TCP 监听器的生命周期（bind → start → stop）、属性访问、
handler_factory 回调、accept 线程行为。
使用真实 socket 和 port=0 避免端口冲突。
"""

import socket
import time
import threading
import pytest
from unittest.mock import MagicMock, patch

from src.auth.context import AuthContext
from src.daemon.listener import Listener


class TestListenerConstruction:
    """Listener 构造与属性测试"""

    def test_construction_stores_params(self):
        """构造函数存储所有参数"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=12345,
            transport="plain", auth_context=ctx,
            publish_shm=True,
        )
        assert listener.transport == "plain"
        assert listener.publish_shm is True

    def test_construction_defaults(self):
        """默认值：ssl_context=None, publish_shm=False"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        assert listener.publish_shm is False

    def test_transport_property(self):
        """transport 属性返回构造时指定的值"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="tls", auth_context=ctx,
        )
        assert listener.transport == "tls"

    def test_port_before_bind(self):
        """bind 前返回配置端口"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=54321,
            transport="plain", auth_context=ctx,
        )
        assert listener.port == 54321


class TestListenerBind:
    """Listener.bind() 测试"""

    def test_bind_returns_actual_port(self):
        """bind 返回实际端口"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        actual_port = listener.bind()
        assert actual_port > 0
        listener.stop()

    def test_port_after_bind(self):
        """bind 后 port 属性返回实际端口"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        listener.bind()
        assert listener.port > 0
        assert listener.port != 0
        listener.stop()

    def test_bind_specific_port(self):
        """bind 指定端口返回该端口"""
        import socket as _socket
        # 使用一个临时 socket 占用端口，确保获取可用端口
        tmp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        tmp.bind(("127.0.0.1", 0))
        free_port = tmp.getsockname()[1]
        tmp.close()

        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=free_port,
            transport="plain", auth_context=ctx,
        )
        actual_port = listener.bind()
        assert actual_port == free_port
        listener.stop()

    def test_bind_creates_socket(self):
        """bind 后内部 socket 不为 None"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        listener.bind()
        # 通过 port 属性间接验证 socket 存在（port 依赖 _sock.getsockname）
        assert listener.port > 0
        listener.stop()


class TestListenerStartStop:
    """Listener.start() + stop() 集成测试"""

    def test_start_calls_handler_factory(self):
        """start 调用 handler_factory 并传入 auth_context"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        listener.bind()

        received_ctx = [None]

        def factory(auth_context):
            received_ctx[0] = auth_context
            return MagicMock()

        listener.start(factory)
        assert received_ctx[0] is ctx
        listener.stop()

    def test_start_then_stop_clean(self):
        """start → stop 不抛异常"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        listener.bind()
        listener.start(lambda ac: MagicMock())
        listener.stop()

    def test_stop_idempotent(self):
        """stop 可多次调用"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        listener.bind()
        listener.start(lambda ac: MagicMock())
        listener.stop()
        listener.stop()  # 不应抛异常

    def test_stop_without_start(self):
        """bind 后直接 stop 不抛异常"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        listener.bind()
        listener.stop()

    def test_stop_without_bind(self):
        """未 bind 直接 stop 不抛异常"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        listener.stop()


class TestListenerAcceptLoop:
    """Listener accept 线程集成测试"""

    def test_accept_handles_connection(self):
        """accept 线程接受连接并调用 handler.handle"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        listener.bind()
        actual_port = listener.port

        handled = threading.Event()

        mock_handler = MagicMock()

        def handle_conn(conn, addr):
            handled.set()
            conn.close()

        mock_handler.handle = handle_conn

        listener.start(lambda ac: mock_handler)

        # 连接到 Listener
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(("127.0.0.1", actual_port))
        # 等待 handler 被调用
        assert handled.wait(timeout=5), "handler.handle 未被调用"
        client.close()
        listener.stop()

    def test_accept_loop_exits_on_stop(self):
        """stop 后 accept 线程正常退出"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="plain", auth_context=ctx,
        )
        listener.bind()
        listener.start(lambda ac: MagicMock())

        # 等待 accept 线程启动
        time.sleep(0.2)
        listener.stop()

        # accept 线程应在 stop 后退出
        if listener._thread is not None:
            listener._thread.join(timeout=5)
            assert not listener._thread.is_alive(), "accept 线程未退出"


class TestListenerTlsPlaceholder:
    """TLS 传输层占位测试（Phase 4 添加 SSL）"""

    def test_tls_transport_without_ssl_context(self):
        """transport=tls 但无 ssl_context 时不崩溃，明文连接"""
        ctx = AuthContext()
        listener = Listener(
            host="127.0.0.1", port=0,
            transport="tls", auth_context=ctx,
            ssl_context=None,
        )
        listener.bind()
        actual_port = listener.port

        handled = threading.Event()

        mock_handler = MagicMock()

        def handle_conn(conn, addr):
            handled.set()
            conn.close()

        mock_handler.handle = handle_conn
        listener.start(lambda ac: mock_handler)

        # 明文连接（无 SSL 握手），handler 应被调用
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(("127.0.0.1", actual_port))
        assert handled.wait(timeout=5), "TLS 占位模式下 handler 未被调用"
        client.close()
        listener.stop()
