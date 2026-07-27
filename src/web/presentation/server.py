"""Web 服务器入口（展示层）。"""

import asyncio
import logging
import os
import socket
import threading
import time
from typing import Optional

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from ..application.adaptive_lock import AdaptiveLockService
from ..application.ports import EventPublisher, HistoryRepository, SessionRepository
from ...fastscreen.ports import FastScreenServicePort
from ...vnc.ports import VncServicePort
from ...vnc import get_novnc_web_dir
from ..history import HistoryStore
from ..infrastructure import (
    CursorLocatorAdapter,
    EventPublisherImpl,
    FastAPIWebSocketTransport,
    FastScreenAdapter,
    HistoryRepositoryAdapter,
    VncAdapter,
    SessionRepositoryAdapter,
    ShellProviderImpl,
    SystemStatsProviderImpl,
    ThreadExecutorImpl,
    WebSocketConnectionContext,
)
from .controllers.fastscreen_controller import create_fastscreen_router
from .controllers.settings_controller import create_settings_router
from .controllers.websocket_controller import WebSocketController

_logger = logging.getLogger("pty-web")

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


class WebServer:
    """PTY-Agent Web 服务器。

    负责启动后台 HTTP/WebSocket 服务，并保持与旧版一致的公共接口。
    """

    def __init__(self, manager, host: str = "127.0.0.1", port: int = 18766):
        self.manager = manager
        self.host = host
        self.port = port

        # 基础设施层
        self._executor = ThreadExecutorImpl()
        self._history_store = HistoryStore()
        self._session_repo: SessionRepository = SessionRepositoryAdapter(manager)
        self._history_repo: HistoryRepository = HistoryRepositoryAdapter(self._history_store)
        self._system_stats = SystemStatsProviderImpl(self._executor)
        self._shell_provider = ShellProviderImpl()

        if manager._history_store is None:
            manager._history_store = self._history_store

        # 连接管理与事件发布
        self._connections: dict = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._publisher: EventPublisher = EventPublisherImpl(
            self._connections, self._get_loop
        )
        # 问题2：自适应排他锁服务（会话级，单例，所有连接共享）
        self._adaptive_lock = AdaptiveLockService()
        self._register_manager_callbacks()

        # VNC 远程桌面适配器（无条件实例化，由 is_available() 决定是否可用；
        # ENABLE_VNC=False 时 get_status() 返回 disabled 状态，前端据此隐藏入口）
        self._vnc_service: Optional[VncServicePort] = None
        try:
            self._vnc_service = VncAdapter()
        except Exception:
            _logger.exception("VncAdapter init failed, VNC disabled")

        # FastScreen 屏幕查看适配器（纯库调用，无子进程；
        # ENABLE_FASTSCREEN=False 或 DLL 加载失败时 is_available() 返回 False）
        self._fastscreen_service: Optional[FastScreenServicePort] = None
        try:
            self._fastscreen_service = FastScreenAdapter()
        except Exception:
            _logger.exception("FastScreenAdapter init failed, FastScreen disabled")

        self._cursor_locator_service = None
        try:
            self._cursor_locator_service = CursorLocatorAdapter()
        except Exception:
            _logger.exception("CursorLocatorAdapter init failed, cursor locator disabled")

        # 控制器
        self._controller = WebSocketController(
            session_repo=self._session_repo,
            history_repo=self._history_repo,
            system_stats=self._system_stats,
            shell_provider=self._shell_provider,
            executor=self._executor,
            publisher=self._publisher,
            adaptive_lock=self._adaptive_lock,
            vnc_service=self._vnc_service,
            fastscreen_service=self._fastscreen_service,
            cursor_locator_service=self._cursor_locator_service,
            # v3: 注入 connections 字典，_cleanup 据此检查同 client_uid 是否还有
            # 其他活跃连接订阅了该 sid（多标签页/刷新场景锁继承）
            connections=self._connections,
        )

        # 生命周期
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[uvicorn.Server] = None
        self._watcher_thread: Optional[threading.Thread] = None
        self._shutdown: bool = False

    def _get_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._loop

    def _register_manager_callbacks(self):
        self._session_repo.set_on_session_created(self._on_manager_session_created)
        self._session_repo.set_on_session_removed(self._on_manager_session_removed)

    def _on_manager_session_created(self, session_id: str):
        _logger.info("manager session_created callback: %s", session_id)
        # 查询 session.uid 一并广播，前端可即时更新 sessions[sid].uid，
        # 避免 sizeSelector 等依赖 uid 的功能在 list 刷新前失效
        uid = ""
        try:
            session = self._session_repo.get_session(session_id)
            if session and getattr(session, "uid", ""):
                uid = session.uid
        except Exception:
            _logger.exception("manager session_created get uid failed sid=%r", session_id)
        self._publisher.publish_session_created(session_id, uid)

    def _on_manager_session_removed(self, session_id: str, exit_code=None, error_message=None):
        _logger.info("manager session_removed callback: %s exit=%s", session_id, exit_code)
        self._publisher.publish_session_removed(session_id, exit_code, error_message)

    def start_background(self):
        _logger.info("WebServer start_background called host=%s port=%d", self.host, self.port)
        self._shutdown = False
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="pty-web-server"
        )
        self._thread.start()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop, daemon=True, name="pty-web-watcher"
        )
        self._watcher_thread.start()
        _logger.info("WebServer background threads started (server=%s watcher=%s)",
                     self._thread.name, self._watcher_thread.name)

    def _watch_loop(self):
        _logger.info("WebServer watcher started (interval=5s)")
        check_count = 0
        while not self._shutdown:
            time.sleep(5)
            if self._shutdown:
                break
            check_count += 1
            thread_alive = self._thread is not None and self._thread.is_alive()
            health_ok = self._health_check()
            if not thread_alive or not health_ok:
                _logger.error(
                    "WebServer unhealthy (check #%d: thread_alive=%s health_ok=%s), restarting",
                    check_count, thread_alive, health_ok,
                )
                try:
                    self._stop_loop()
                    if self._thread and self._thread.is_alive():
                        self._thread.join(timeout=3)
                    self._thread = threading.Thread(
                        target=self._run_loop, daemon=True, name="pty-web-server"
                    )
                    self._thread.start()
                    _logger.info("WebServer restart thread started (attempt after check #%d)", check_count)
                except Exception:
                    _logger.exception("WebServer restart failed (after check #%d)", check_count)
            elif check_count % 12 == 0:
                _logger.debug("WebServer watcher check #%d: healthy", check_count)
        _logger.info("WebServer watcher stopped (total checks=%d)", check_count)

    def _health_check(self) -> bool:
        start = time.monotonic()
        # 0.0.0.0 不能作为连接目标，用 127.0.0.1 检查本地监听
        check_host = "127.0.0.1" if self.host == "0.0.0.0" else self.host
        try:
            with socket.create_connection((check_host, self.port), timeout=2):
                elapsed_ms = (time.monotonic() - start) * 1000
                _logger.debug("WebServer health check OK (%.1fms)", elapsed_ms)
                return True
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            _logger.warning("WebServer health check FAILED (%.1fms): %s", elapsed_ms, e)
            return False

    def _stop_loop(self):
        # 检查 loop 是否已关闭，避免 run_coroutine_threadsafe 抛 RuntimeError 产生 warning
        if self._loop and not self._loop.is_closed() and self._server:
            try:
                asyncio.run_coroutine_threadsafe(self._stop_coro(), self._loop)
            except Exception as e:
                _logger.warning("WebServer _stop_loop failed: %s", e)

    def _run_loop(self):
        _logger.info("WebServer _run_loop entered (thread=%s)", threading.current_thread().name)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.create_task(self._start())
            _logger.info("WebServer _start task created, entering run_forever")
            self._loop.run_forever()
        except Exception:
            _logger.exception("WebServer loop error")
        finally:
            _logger.info("WebServer _run_loop exiting")
            try:
                self._loop.close()
                _logger.info("WebServer event loop closed")
            except Exception:
                _logger.exception("WebServer loop close error")

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="PTY-Agent Web", docs_url=None, redoc_url=None)

        @app.middleware("http")
        async def _request_logging_middleware(request: Request, call_next):
            """HTTP 请求日志中间件 + 静态文件缓存控制。"""
            start = time.monotonic()
            method = request.method
            path = request.url.path
            remote = request.client.host if request.client else "-"
            try:
                response = await call_next(request)
                # 静态文件始终重新验证，确保 ES 模块更新后浏览器获取最新版本
                if path.startswith("/static/"):
                    response.headers.setdefault(
                        "Cache-Control", "no-cache, must-revalidate")
                elapsed_ms = (time.monotonic() - start) * 1000
                _logger.info(
                    "HTTP %s %s from %s -> %d (%.1fms)",
                    method, path, remote, response.status_code, elapsed_ms,
                )
                return response
            except Exception:
                elapsed_ms = (time.monotonic() - start) * 1000
                _logger.exception(
                    "HTTP %s %s from %s -> ERROR (%.1fms)",
                    method, path, remote, elapsed_ms,
                )
                raise

        @app.get("/")
        async def _index():
            _logger.info("WebServer HTTP / request")
            try:
                return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
            except Exception:
                _logger.exception("WebServer HTTP / error")
                raise

        # VNC 前端静态资源（必须在 /static 之前 mount，避免被通配覆盖）
        # 路径示例：/static/novnc/vnc.html → src/vnc/src/static/novnc/vnc.html
        if self._vnc_service is not None:
            try:
                novnc_web_dir = get_novnc_web_dir()
                if novnc_web_dir.exists():
                    app.mount(
                        "/static/novnc",
                        StaticFiles(directory=str(novnc_web_dir)),
                        name="static-novnc",
                    )
                    _logger.info("noVNC static mounted at /static/novnc -> %s", novnc_web_dir)
                else:
                    _logger.warning("noVNC web dir not found, skip mount: %s", novnc_web_dir)
            except Exception:
                _logger.exception("noVNC static mount failed")

        # FastScreen 流媒体端点（HTTP MJPEG + WS H264 MSE + WS WebCodecs）
        # 复用 StreamManager 多客户端共享会话；按需连接，断开即停止捕获
        if self._fastscreen_service is not None:
            try:
                fs_router = create_fastscreen_router(self._fastscreen_service)
                app.include_router(fs_router)
                _logger.info(
                    "FastScreen router mounted: available=%s",
                    self._fastscreen_service.is_available(),
                )
            except Exception:
                _logger.exception("FastScreen router mount failed")

        # VNC WebSocket 代理端点（替代 websockify 子进程）
        # noVNC iframe 连接 ws://host:port/vnc/websockify，本端点代理到 localhost:vnc_port
        # 统一到守护进程单一端口，无需独立 websockify 端口
        @app.websocket("/vnc/websockify")
        async def _vnc_websockify(ws: WebSocket):
            await ws.accept()
            remote = ws.client.host if ws.client else "-"
            # 从 VNC 服务获取当前 vnc_port
            vnc_port = None
            if self._vnc_service is not None:
                try:
                    status = await self._executor.run(self._vnc_service.get_status)
                    vnc_port = status.get("vnc_port")
                except Exception:
                    _logger.exception("VNC proxy: get_status failed")
            if not vnc_port:
                _logger.warning("VNC proxy: VNC not running, reject from %s", remote)
                await ws.close(code=1011, reason="VNC not running")
                return
            _logger.info("VNC proxy: %s -> localhost:%d", remote, vnc_port)
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", vnc_port)
            except Exception as e:
                _logger.exception("VNC proxy: connect to VNC TCP failed: %s", e)
                await ws.close(code=1011, reason="VNC TCP connect failed")
                return
            # 双向代理：WS→TCP + TCP→WS
            async def _ws_to_tcp():
                try:
                    while True:
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            break
                        if "bytes" in msg and msg["bytes"] is not None:
                            writer.write(msg["bytes"])
                            await writer.drain()
                        elif "text" in msg and msg["text"] is not None:
                            writer.write(msg["text"].encode("utf-8"))
                            await writer.drain()
                except Exception:
                    pass
                finally:
                    try:
                        writer.close()
                    except Exception:
                        pass

            async def _tcp_to_ws():
                try:
                    while True:
                        data = await reader.read(65536)
                        if not data:
                            break
                        await ws.send_bytes(data)
                except Exception:
                    pass
                finally:
                    try:
                        await ws.close()
                    except Exception:
                        pass

            await asyncio.gather(_ws_to_tcp(), _tcp_to_ws(), return_exceptions=True)
            _logger.info("VNC proxy: closed from %s", remote)

        # 设置 REST 端点（/api/settings*）
        # 默认值来自 web.toml，用户覆盖项持久化到 ~/.pty-agent/web_user_choice.json
        try:
            settings_router = create_settings_router()
            app.include_router(settings_router)
            _logger.info("Settings router mounted at /api/settings")
        except Exception:
            _logger.exception("Settings router mount failed")

        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

        @app.websocket("/ws")
        async def _ws(ws: WebSocket):
            remote = ws.client.host if ws.client else "-"
            # v3: 从 URL query 读取 client_uid（前端 localStorage 持久化，刷新不变）
            # 用于自适应锁的持有者标识，使锁可跨重连/刷新恢复
            client_uid = ws.query_params.get("clientUid") or ""
            _logger.info("WebSocket /ws connect from %s clientUid=%s", remote, client_uid or "(none)")
            await ws.accept()
            _logger.info("WebSocket /ws accepted for %s", remote)

            transport = FastAPIWebSocketTransport(ws)
            conn_id = id(transport)
            context = WebSocketConnectionContext(client_uid=client_uid or None)
            queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
            self._connections[conn_id] = {
                "transport": transport,
                "context": context,
                "queue": queue,
            }
            try:
                await self._controller.handle(transport, context, queue)
                _logger.info("WebSocket /ws handler returned for %s (closed=%s)", remote, transport.closed)
            except Exception:
                _logger.exception("WebSocket /ws error from %s", remote)
            finally:
                self._connections.pop(conn_id, None)

        return app

    async def _start(self):
        _logger.info("WebServer _start entered host=%s port=%d", self.host, self.port)
        app = self._build_app()
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            loop="none",
        )
        self._server = uvicorn.Server(config)
        heartbeat_task = None
        try:
            heartbeat_task = self._start_heartbeat()
            await self._server.serve()
            _logger.info("WebServer uvicorn serve exited")
        except OSError as e:
            _logger.error("WebServer bind failed %s:%d: %s", self.host, self.port, e)
        except Exception as e:
            # 捕获 serve() 的非 OSError 异常（如端口占用时 uvicorn 内部抛出的 RuntimeError），
            # 避免异常被 task 吞掉导致诊断信息缺失（AGENTS.md: 完备的日志系统）
            _logger.exception("WebServer serve error %s:%d: %s", self.host, self.port, e)
        finally:
            _logger.info("WebServer _start finished")
            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            if self._loop:
                self._loop.stop()

    def _start_heartbeat(self):
        async def heartbeat():
            beat = 0
            while True:
                await asyncio.sleep(30)
                beat += 1
                active_conns = len(self._connections)
                _logger.debug("WebServer heartbeat #%d: serving on %s:%d, active_ws=%d",
                              beat, self.host, self.port, active_conns)
        if self._loop:
            return asyncio.ensure_future(heartbeat(), loop=self._loop)
        return None

    def stop(self):
        _logger.info("WebServer stop called")
        self._shutdown = True
        if self._loop and self._server:
            try:
                asyncio.run_coroutine_threadsafe(self._stop_coro(), self._loop)
            except Exception as e:
                _logger.warning("WebServer stop call failed: %s", e)

    async def _stop_coro(self):
        _logger.info("WebServer _stop_coro entered")
        try:
            if self._server:
                self._server.should_exit = True
                try:
                    await self._server.shutdown()
                except Exception:
                    _logger.exception("WebServer server shutdown error")
                self._server = None
        except Exception:
            _logger.exception("WebServer server stop error")
        # 清理 VNC 子进程（winvnc）
        if self._vnc_service is not None:
            try:
                self._vnc_service.cleanup()
            except Exception:
                _logger.exception("VNC cleanup error during stop")
        # 清理 FastScreen 捕获会话
        if self._fastscreen_service is not None:
            try:
                self._fastscreen_service.cleanup()
            except Exception:
                _logger.exception("FastScreen cleanup error during stop")
        _logger.info("WebServer _stop_coro exit requested")
