"""Web 服务器入口（展示层）。"""

import asyncio
import logging
import os
import socket
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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
from ..infrastructure.auth import SessionStore
from .controllers.auth_controller import create_auth_router, validate_request_auth, validate_ws_auth
from .controllers.fastscreen_controller import create_fastscreen_router
from .controllers.settings_controller import create_settings_router
from .controllers.websocket_controller import WebSocketController

_logger = logging.getLogger("pty-web")

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


class WebServer:
    """PTY-Agent Web 服务器。

    负责启动后台 HTTP/WebSocket 服务，并保持与旧版一致的公共接口。
    """

    def __init__(self, manager, host: str = "127.0.0.1", port: int = 18766,
                 password_hash: str = ""):
        self.manager = manager
        self.host = host
        self.port = port

        # 密码认证：空哈希=免密登录（仅填服务器地址），非空=需密码校验
        self._auth_enabled = bool(password_hash)
        self._session_store = SessionStore()
        self._password_hash: str = password_hash
        if self._auth_enabled:
            _logger.info("Web auth enabled (password hash set)")
        else:
            _logger.info("Web auth disabled (no password, login page allows empty password)")

        # 基础设施层
        self._executor = ThreadExecutorImpl()
        self._history_store = HistoryStore()
        self._session_repo: SessionRepository = SessionRepositoryAdapter(manager)
        self._history_repo: HistoryRepository = HistoryRepositoryAdapter(self._history_store)
        self._system_stats = SystemStatsProviderImpl(self._executor)
        self._shell_provider = ShellProviderImpl()

        existing = getattr(manager, '_history_store', None)
        if existing is None:
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

    def _get_loopback_allowed_hosts(self) -> list:
        """当服务监听在回环地址时，返回允许的 Host 头值列表。

        仅当 self.host 为回环地址（127.x.x.x / ::1 / localhost）时启用。
        返回空列表表示不启用校验（非回环监听）。

        Returns:
            允许的 Host 头值列表（全小写），如 ["127.0.0.1:18766", "127.0.0.1", "localhost:18766", "localhost"]
        """
        if not self._is_loopback_host(self.host):
            return []
        port = self.port
        hosts = set()
        # 127.0.0.1 系列
        hosts.add(f"127.0.0.1:{port}")
        hosts.add("127.0.0.1")
        # localhost 系列
        hosts.add(f"localhost:{port}")
        hosts.add("localhost")
        # [::1] 系列（IPv6 回环）
        hosts.add(f"[::1]:{port}")
        hosts.add("[::1]")
        # 如果 host 是 127.x.x.x 的其他地址，也加入
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            hosts.add(f"{self.host}:{port}")
            hosts.add(self.host)
        return sorted(hosts)

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        """判断监听地址是否为回环地址。

        Args:
            host: 监听地址字符串

        Returns:
            True 表示回环地址
        """
        if host in ("localhost", "::1"):
            return True
        # 127.x.x.x 整个段都是回环
        try:
            parts = host.split(".")
            if len(parts) == 4 and parts[0] == "127":
                return all(0 <= int(p) <= 255 for p in parts)
        except (ValueError, IndexError):
            pass
        return False

    def _validate_ws_auth(self, ws: WebSocket) -> bool:
        """校验 WebSocket 连接的认证 token 是否有效。

        同时支持 authToken query param（跨域）和 Cookie（同源）。
        未启用认证时直接返回 True。

        Args:
            ws: FastAPI WebSocket 实例

        Returns:
            True 表示已认证或认证未启用
        """
        if not self._auth_enabled or self._session_store is None:
            return True
        return validate_ws_auth(ws, self._session_store)

    def _get_http_auth_validator(self) -> Optional[Callable[[Request], bool]]:
        """返回 HTTP 请求认证校验函数，供子路由使用。

        未启用认证时返回 None（路由方据此跳过校验）。
        """
        if not self._auth_enabled or self._session_store is None:
            return None
        store = self._session_store

        def _validator(request: Request) -> bool:
            return validate_request_auth(request, store)

        return _validator

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="PTY-Agent Web", docs_url=None, redoc_url=None)

        # CORS 中间件：允许跨域访问认证端点（登录页可能从其他源发起请求）
        # 使用 allow_origin_regex 而非 allow_origins=["*"]，因为 credentials=True 时
        # 浏览器不允许 Access-Control-Allow-Origin 为通配符 *，必须回显具体 Origin
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=".*",
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
            allow_credentials=True,
        )

        # 回环地址 Host/Origin/Referer 校验中间件
        # 当服务监听在 127.0.0.1 等本地回环地址时，严格校验请求的 Host 头
        # 和 Origin/Referer 头，防止 DNS 重绑定攻击。校验失败返回 403。
        _loopback_allowed_hosts = self._get_loopback_allowed_hosts()
        if _loopback_allowed_hosts:
            _logger.info(
                "Loopback host validation enabled, allowed hosts: %s",
                _loopback_allowed_hosts,
            )

            @app.middleware("http")
            async def _loopback_host_middleware(request: Request, call_next):
                host_header = request.headers.get("host", "")
                if host_header.lower() not in _loopback_allowed_hosts:
                    remote = request.client.host if request.client else "-"
                    _logger.warning(
                        "Loopback host check rejected: Host=%r from %s (allowed=%s)",
                        host_header, remote, _loopback_allowed_hosts,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={"error": "forbidden", "message": "Invalid Host header"},
                    )
                # 校验 Origin 头（若存在）
                origin = request.headers.get("origin", "")
                if origin:
                    parsed = urlparse(origin)
                    origin_host = parsed.hostname or ""
                    origin_port = parsed.port
                    if origin_port:
                        origin_host_port = f"{origin_host}:{origin_port}".lower()
                    else:
                        origin_host_port = origin_host.lower()
                    # 构造允许的 origin host 列表（含/不含端口）
                    if origin_host_port not in _loopback_allowed_hosts and origin_host.lower() not in [
                        h.split(":")[0] for h in _loopback_allowed_hosts
                    ]:
                        remote = request.client.host if request.client else "-"
                        _logger.warning(
                            "Loopback origin check rejected: Origin=%r from %s",
                            origin, remote,
                        )
                        return JSONResponse(
                            status_code=403,
                            content={"error": "forbidden", "message": "Invalid Origin"},
                        )
                return await call_next(request)

        # 认证路由（/api/auth/* + /login）——始终挂载
        # 无密码哈希时，login 端点允许空密码直接创建会话（等效免密登录）
        try:
            auth_router = create_auth_router(self._session_store, self._password_hash)
            app.include_router(auth_router)
            _logger.info("Auth router mounted at /api/auth + /login")
        except Exception:
            _logger.exception("Auth router mount failed")

        # 登录页路由（密码为空时也需要能访问 login.html）
        @app.get("/login", response_class=FileResponse)
        async def _login_page():
            return FileResponse(os.path.join(_STATIC_DIR, "login.html"))

        # HTTP 请求认证校验函数（供子路由使用）
        _http_auth = self._get_http_auth_validator()

        @app.middleware("http")
        async def _request_logging_middleware(request: Request, call_next):
            """HTTP 请求日志中间件 + 静态文件缓存控制。"""
            start = time.monotonic()
            method = request.method
            path = request.url.path
            remote = request.client.host if request.client else "-"
            try:
                response = await call_next(request)
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

        # VNC 前端静态资源（必须在 / 之前 mount，避免被通配覆盖）
        # 路径示例：/static/novnc/vnc.html → src/web/static/vendor/novnc/vnc.html
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
                fs_router = create_fastscreen_router(self._fastscreen_service, auth_validator=_http_auth, session_store=self._session_store)
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

            # 端点级认证校验
            if not self._validate_ws_auth(ws):
                _logger.warning("VNC proxy: auth failed from %s", remote)
                try:
                    await ws.close(code=4001, reason="Unauthorized")
                except Exception:
                    pass
                return

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
            settings_router = create_settings_router(auth_validator=_http_auth)
            app.include_router(settings_router)
            _logger.info("Settings router mounted at /api/settings")
        except Exception:
            _logger.exception("Settings router mount failed")

        @app.websocket("/ws")
        async def _ws(ws: WebSocket):
            remote = ws.client.host if ws.client else "-"
            # v3: 从 URL query 读取 client_uid（前端 localStorage 持久化，刷新不变）
            # 用于自适应锁的持有者标识，使锁可跨重连/刷新恢复
            client_uid = ws.query_params.get("clientUid") or ""
            _logger.info("WebSocket /ws connect from %s clientUid=%s", remote, client_uid or "(none)")
            await ws.accept()

            # 端点级认证校验：Cookie 无效时发送 auth_required 后关闭
            if not self._validate_ws_auth(ws):
                _logger.warning("WebSocket /ws auth failed from %s", remote)
                try:
                    await ws.send_json({"type": "auth_required"})
                except Exception:
                    pass
                try:
                    await ws.close(code=4001, reason="Unauthorized")
                except Exception:
                    pass
                return

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

        app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

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
