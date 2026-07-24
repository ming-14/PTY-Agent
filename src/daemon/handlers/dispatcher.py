import json
import socket
import time
import traceback
import logging

from ...protocol.message import Message
from ...protocol.response import Response
from ...auth.context import AuthContext
from ...session.manager import SessionManager
from .base import DaemonHandler, HandlerContext
from .utils import get_detail
from .exec_handler import ExecHandler
from .send_handler import SendHandler
from .read_handler import ReadHandler
from .kill_handler import KillHandler
from .mouse_handler import MouseHandler
from .events_handler import EventsHandler
from .closewin_handler import CloseWinHandler
from .status_handler import StatusHandler
from .list_handler import ListHandler
from .stop_handler import StopHandler
from .wait_handler import WaitHandler

_logger = logging.getLogger("pty-daemon")


class DaemonDispatcher:
    def __init__(self, manager: SessionManager, auth_context: AuthContext,
                 server=None):
        # AuthContext 持有签名器与认证器，认证器解包给 HandlerContext 供业务层使用
        self._auth_context = auth_context
        self._ctx = HandlerContext(manager, auth_context.authenticator, server)
        self._registry: dict[str, DaemonHandler] = self._build_registry()

    def _build_registry(self) -> dict[str, DaemonHandler]:
        return {
            "exec": ExecHandler(),
            "send": SendHandler(),
            "read": ReadHandler(),
            "kill": KillHandler(),
            "mouse": MouseHandler(),
            "events": EventsHandler(),
            "closewin": CloseWinHandler(),
            "status": StatusHandler(),
            "list": ListHandler(),
            "stop": StopHandler(),
            "wait": WaitHandler(),
        }

    def dispatch(self, conn, msg: dict):
        msg_type = msg.get("type", "")
        session_id = msg.get("id", "")
        detail = get_detail(msg)

        if msg_type not in ("ping",) and self._ctx.authenticator:
            if not self._ctx.authenticator.authenticate(msg):
                _logger.warning("认证失败 (type=%s id=%s)", msg_type, session_id)
                Message.send(conn, Response.error("Authentication failed"))
                return

        _logger.info("请求: %s id=%s %s", msg_type, session_id, detail)
        msg["_t_start"] = time.monotonic()

        if msg_type == "ping":
            Message.send(conn, {"type": "pong"}, skip_sign=True)
            return

        handler = self._registry.get(msg_type)
        if handler:
            handler.handle(self._ctx, conn, msg)
        else:
            err = f"未知指令类型: {msg_type}"
            _logger.warning(err)
            Message.send(conn, Response.error(err))

    def handle(self, conn, addr):
        # 设置当前连接线程的签名器（线程局部存储）
        # 双端口架构下每个 Listener 的连接线程独立设置，互不干扰
        Message.set_outbound_signer(self._auth_context.outbound_signer)
        Message.set_inbound_verifier(self._auth_context.inbound_verifier)
        try:
            msg = Message.recv(conn)
            if msg is None:
                # recv 返回 None 可能是连接关闭或签名验证失败
                # 签名验证失败时客户端仍在等待响应，尝试发送 Authentication failed
                # 连接已关闭时 send 会抛异常，忽略即可
                _logger.warning("recv 返回 None，可能签名验证失败或连接关闭")
                try:
                    Message.send(conn, Response.error("Authentication failed"))
                except Exception:
                    pass
                return
            self.dispatch(conn, msg)
        except json.JSONDecodeError:
            _logger.error("JSON 解析失败")
            try:
                Message.send(conn, Response.error("Invalid request: JSON parse failed"))
            except Exception:
                pass
        except (BrokenPipeError, ConnectionError, OSError) as e:
            _logger.warning("客户端连接异常: %s", e)
        except Exception as e:
            tb = traceback.format_exc()
            _logger.error("请求处理异常: %s", e)
            _logger.error(tb)
            try:
                Message.send(conn, Response.error("Internal server error"))
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
