import json
import time
import traceback

from ...auth.context import AuthContext
from ...plugins.base import HANDLED, ProcessPluginContext
from ...plugins.io import PluginIO
from ...protocol.message import Message
from ...protocol.response import Response
from ...session.manager import SessionManager
from .base import DaemonHandler, HandlerContext
from .closewin_handler import CloseWinHandler
from .events_handler import EventsHandler
from .exec_handler import ExecHandler
from .kill_handler import KillHandler
from .list_handler import ListHandler
from .mouse_handler import MouseHandler
from .plugin_handler import PluginHandler
from .read_handler import ReadHandler
from .send_handler import SendHandler
from .status_handler import StatusHandler
from .stop_handler import StopHandler
from .utils import get_detail
from .wait_handler import WaitHandler
from .workflow_handler import WorkflowHandler
from ...logging import get_logger, bind, unbind

_logger = get_logger("pty-daemon")


class PluginMessageHandler(DaemonHandler):
    """进程级插件消息路由适配器

    将插件声明的 message_types 路由到其 handle_message：
    - 构造 ProcessPluginContext（manager + io 通道，needs_io 时注入）
    - 返回 dict 原样作为响应发送（响应签名由 Message.send 完成）
    - 返回 None / 抛异常 → 统一回 error，异常隔离不中断 daemon
    """

    def __init__(self, plugin):
        self._plugin = plugin

    def handle(self, ctx: HandlerContext, conn, msg: dict):
        io = PluginIO(conn) if self._plugin.needs_io else None
        pctx = ProcessPluginContext(ctx.manager, self._plugin, io)
        try:
            result = self._plugin.handle_message(pctx, msg)
        except Exception:
            _logger.exception(
                "进程级插件 %s 处理消息 %s 异常 (id=%s)",
                self._plugin.name,
                msg.get("type"),
                msg.get("id"),
            )
            result = None
        if result is HANDLED:
            return
        if result is None:
            Message.send(
                conn,
                Response.error(
                    "插件 %s 未处理消息: %s" % (self._plugin.name, msg.get("type"))
                ),
            )
            return
        Message.send(conn, result)


class DaemonDispatcher:
    def __init__(self, manager: SessionManager, auth_context: AuthContext, server=None):
        # AuthContext 持有签名器与认证器，认证器解包给 HandlerContext 供业务层使用
        self._auth_context = auth_context
        self._ctx = HandlerContext(manager, auth_context.authenticator, server)
        self._registry: dict[str, DaemonHandler] = self._build_registry()

    def _build_registry(self) -> dict[str, DaemonHandler]:
        registry: dict[str, DaemonHandler] = {
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
            "plugin": PluginHandler(),
            "workflow": WorkflowHandler(),
        }
        # 进程级插件路由：插件声明的 message_types 注册到派发表；
        # 与内置 handler 冲突时内置优先（核心命令权威），记录警告
        plugin_registry = (
            getattr(self._ctx.manager, "plugin_registry", None)
            if self._ctx.manager
            else None
        )
        if plugin_registry is not None:
            for name, inst in plugin_registry.process_instances().items():
                for mtype in inst.message_types:
                    if mtype in registry:
                        _logger.warning(
                            "消息类型 %s 已被内置 handler 占用，插件 %s 的声明跳过",
                            mtype,
                            name,
                        )
                        continue
                    registry[mtype] = PluginMessageHandler(inst)
                    _logger.debug("消息类型 %s 由进程级插件 %s 接管", mtype, name)
        return registry

    def dispatch(self, conn, msg: dict):
        msg_type = msg.get("type", "")
        session_id = msg.get("id", "")
        detail = get_detail(msg)

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
        _ctx_token = bind(connection_id=id(conn))
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
            # 连接级握手认证：验签已隐含身份（token 凭据有效性 / pubkey 白名单 /
            # basic 密码均被签名内容覆盖），此处仅每连接校验一次，后续消息只验签
            if msg.get("type") != "ping" and self._ctx.authenticator:
                if not self._ctx.authenticator.authenticate(msg):
                    _logger.warning(
                        "认证失败 (type=%s id=%s)", msg.get("type"), msg.get("id")
                    )
                    Message.send(conn, Response.error("Authentication failed"))
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
            unbind(_ctx_token)
            try:
                conn.close()
            except OSError:
                pass
