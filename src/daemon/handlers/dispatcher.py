from __future__ import annotations

import json
import time
import traceback

from ...auth.context import AuthContext
from ...plugins.base import HANDLED, ProcessPluginContext
from ...plugins.decorate import decorate_builtin_response
from ...plugins.io import PluginIO
from ...protocol.envelope import unwrap as _env_unwrap, wrap_response as _env_wrap_response
from ...protocol.message import Message
from ...protocol.response import Response
from ...session.manager import SessionManager
from .base import DaemonHandler
from .file_handler import FileHandler
from ...execution.context import HandlerContext
from .attend_handler import AttendHandler
from .closewin_handler import CloseWinHandler
from .events_handler import EventsHandler
from .exec_handler import ExecHandler
from .kill_handler import KillHandler
from .list_handler import ListHandler
from .mouse_handler import MouseHandler
from .notice_handler import NoticeHandler
from .plugin_handler import PluginHandler
from .read_handler import ReadHandler
from .send_handler import SendHandler
from .set_default_handler import GetDefaultsHandler, SetDefaultHandler
from .status_handler import StatusHandler
from .stop_handler import StopHandler
from ...execution.utils import get_detail
from .wait_handler import WaitHandler
from .workflow_handler import WorkflowHandler
from ...logging import get_logger, bind, unbind

_logger = get_logger("pty-daemon")

# 自动消费通知的操作型命令：操作了某会话（exec/send/read/mouse/kill）后
# 该会话的通知已无意义（用户正在主动操作/查看），自动移入归档。
# 查询型命令（plugin ls/list/status 等）仅读取状态，不消费通知——
# 否则客户端 read/send 前自动发的 plugin ls 会误删刚发布的回合完成通知。
_AUTO_CONSUME_COMMANDS = frozenset(("exec", "send", "read", "mouse", "kill"))


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
        manifest = getattr(self._plugin, "manifest", None)
        io = PluginIO(conn) if manifest is not None and manifest.needs_io else None
        env = None
        if ctx.manager is not None:
            pr = getattr(ctx.manager, "plugin_registry", None)
            if pr is not None:
                env = pr.environment
        pctx = ProcessPluginContext(ctx.manager, self._plugin, io, environment=env)
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
        self._builtin_types: set = set()
        self._plugin_handlers: dict = {}  # 进程级插件消息类型 → PluginMessageHandler
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
            "notice": NoticeHandler(),
            "plugin": PluginHandler(),
            "workflow": WorkflowHandler(),
            "attend": AttendHandler(),
            "set_default": SetDefaultHandler(),
            "get_defaults": GetDefaultsHandler(),
            "file_read": FileHandler(),
            "file_write": FileHandler(),
            "file_edit": FileHandler(),
            "file_grep": FileHandler(),
            "file_glob": FileHandler(),
            "file_upload_start": FileHandler(),
            "file_download_start": FileHandler(),
        }
        self._builtin_types = set(registry)
        # 进程级插件路由：插件声明的 message_types 注册到派发表；
        # 与内置 handler 冲突时内置优先（核心命令权威），记录警告
        plugin_registry = (
            getattr(self._ctx.manager, "plugin_registry", None)
            if self._ctx.manager
            else None
        )
        if plugin_registry is not None:
            plugin_registry.set_change_callback(self._sync_plugin_handlers)
            self._sync_plugin_handlers()
        return registry

    def _sync_plugin_handlers(self) -> None:
        """同步进程级插件消息路由（enable/disable/reload 后由注册表回调）

        与内置 handler 冲突时内置优先；插件间同类型冲突按插件名序先者胜。
        """
        new: dict = {}
        plugin_registry = (
            getattr(self._ctx.manager, "plugin_registry", None)
            if self._ctx.manager
            else None
        )
        if plugin_registry is not None:
            for name in sorted(plugin_registry.process_instances()):
                inst = plugin_registry.process_instances()[name]
                for mtype in inst.manifest.message_types:
                    if mtype in self._builtin_types:
                        _logger.warning(
                            "消息类型 %s 已被内置 handler 占用，插件 %s 的声明跳过",
                            mtype,
                            name,
                        )
                        continue
                    if mtype in new:
                        _logger.warning(
                            "消息类型 %s 已被插件 %s 占用，插件 %s 的声明跳过",
                            mtype,
                            new[mtype]._plugin.name,
                            name,
                        )
                        continue
                    new[mtype] = PluginMessageHandler(inst)
                    _logger.debug("消息类型 %s 由进程级插件 %s 接管", mtype, name)
        self._plugin_handlers = new

    def dispatch(self, conn, body: dict, type_: str = None):
        body = body if isinstance(body, dict) else {}
        msg_type = type_ or body.get("type", "")
        session_id = body.get("id", "")
        detail = get_detail(body)

        _logger.info("请求: %s id=%s %s", msg_type, session_id, detail)
        body["_t_start"] = time.monotonic()

        if msg_type == "ping":
            Message.send(conn, {"type": "pong"}, skip_sign=True)
            return

        # 操作型命令（send/read/mouse/kill/exec）请求到达时立即消费该会话通知，
        # 而非等 handler 响应时才消费——send 可能等待 40s 输出，这期间通知
        # 应提前清空，避免用户后续 wait 收到过期通知。
        if session_id and msg_type in _AUTO_CONSUME_COMMANDS:
            try:
                nm = getattr(getattr(self._ctx, "server", None), "notify_manager", None)
                if nm is not None:
                    nm.consume_by_session(session_id)
            except Exception:
                pass

        handler = self._registry.get(msg_type)
        if handler is None:
            handler = self._plugin_handlers.get(msg_type)
        if handler:
            handler.handle(self._ctx, conn, body)
        else:
            err = f"未知指令类型: {msg_type}"
            _logger.warning(err)
            Message.send(conn, Response.error(err))

    def handle(self, conn, addr):
        # 设置当前连接线程的签名器（线程局部存储）
        # 双端口架构下每个 Listener 的连接线程独立设置，互不干扰
        Message.set_outbound_signer(self._auth_context.outbound_signer)
        Message.set_inbound_verifier(self._auth_context.inbound_verifier)
        # 出站响应包装（线程局部）：先经插件装饰链（decorateTypes 匹配），
        # 再把 handler 构建的扁平响应体套响应信封并分组
        # 同时注入全局通知待消费计数（pendingNotifCount，供 presenter 提示）
        def _response_wrapper(body):
            body = decorate_builtin_response(self._ctx.manager, body)
            if isinstance(body, dict):
                nm = getattr(getattr(self._ctx, "server", None), "notify_manager", None)
                if nm is not None:
                    # 通知消费已提前到请求到达时（dispatch 中按操作型命令白名单执行），
                    # 此处只注入全局通知待消费计数（pendingNotifCount，供 presenter 提示）。
                    # 不再响应时消费：send 可能等待 40s 输出，期间新发布的回合完成通知
                    # 应保留给用户 wait 消费，而非被本次响应误删。
                    n = nm.pending_count()
                    if n > 0 and "pendingNotifCount" not in body:
                        body["pendingNotifCount"] = n
            return _env_wrap_response(body)

        Message.set_outbound_response_wrapper(_response_wrapper)
        _ctx_token = bind(connection_id=id(conn))
        try:
            msg = Message.recv(conn)
            if msg is None:
                # recv 返回 None 可能是连接关闭、读超时（慢客户端未在
                # CONNECTION_READ_TIMEOUT 内发完整请求）或签名验证失败。
                # 签名验证失败时客户端仍在等待响应，尝试发送 Authentication
                # failed（读超时场景下该消息对慢客户端有误导性，但连接即将
                # 关闭，可接受；message.py 已在日志区分具体原因）；
                # 连接已关闭时 send 会抛异常，忽略即可。
                _logger.warning(
                    "recv 返回 None（连接关闭/读超时/签名验证失败），关闭连接"
                )
                try:
                    Message.send(conn, Response.error("Authentication failed."))
                except Exception:
                    pass
                return
            # 读请求阶段结束：恢复无超时写。读超时仅约束"等请求"阶段；
            # 响应可能很大（如 screenBufferZ 可达 MB 级），写超时过短会误杀
            # 正常大响应，故写侧不设超时，写阻塞由 MAX_CONNECTIONS 上限兜底
            conn.settimeout(None)
            # 拆请求信封 → 扁平 body（分组负载 op/condition/output/io 还原为业务字段）；
            # 认证基于含凭证（token/password/pubkey_fp）的原始信封；非信封消息认证 body
            type_, body, envelope = _env_unwrap(msg)
            auth_msg = envelope if envelope is not None else body
            # 连接级握手认证：验签已隐含身份（token 凭据有效性 / pubkey 白名单 /
            # basic 密码均被签名内容覆盖），此处仅每连接校验一次，后续消息只验签
            if type_ != "ping" and self._ctx.authenticator:
                if not self._ctx.authenticator.authenticate(auth_msg):
                    _logger.warning(
                        "认证失败 (type=%s id=%s)", type_, body.get("id")
                    )
                    Message.send(conn, Response.error("Authentication failed."))
                    return
            self.dispatch(conn, body, type_=type_)
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
