"""Workflow 命令处理 — WorkflowHandler

workflow run/list/show/cancel 消息路由：
- run: 接收 YAML 定义文本（client 侧读文件传来，跨机可用）→ 解析校验 → 后台执行
- list: 运行列表（含已结束）
- show: 单次运行完整状态（步骤状态 + 事件日志）
- cancel: 请求取消（置位取消事件，执行中步骤尽快返回）
"""


from ...config.daemon import WORKFLOW_MAX_FILE_SIZE
from ...protocol.message import Message
from ...protocol.response import Response
from .base import DaemonHandler, HandlerContext
from ...logging import get_logger

_logger = get_logger("pty-daemon")


class WorkflowHandler(DaemonHandler):
    def handle(self, ctx: HandlerContext, conn, msg: dict):
        # lazy import：avoid 与 src.workflow → daemon.handlers 的导入环
        # （workflow 引擎依赖 handlers 工具函数，此处反向引用必须延后）
        from ...workflow import DefinitionError, parse_definition

        wm = getattr(ctx.server, "workflow_manager", None) if ctx.server else None
        if wm is None:
            Message.send(conn, Response.error("workflow 服务不可用"))
            return

        action = msg.get("action")
        if action == "run":
            self._handle_run(wm, conn, msg, parse_definition, DefinitionError)
        elif action == "list":
            Message.send(
                conn, {"type": "workflow", "action": "list", "runs": wm.list_runs()}
            )
        elif action == "show":
            self._handle_show(wm, conn, msg)
        elif action == "cancel":
            self._handle_cancel(wm, conn, msg)
        else:
            Message.send(conn, Response.error("unknown workflow action: %s" % action))

    def _handle_run(self, wm, conn, msg: dict, parse_definition, definition_error):
        text = msg.get("definition")
        if not isinstance(text, str) or not text.strip():
            Message.send(conn, Response.error("definition is required"))
            return
        if len(text) > WORKFLOW_MAX_FILE_SIZE:
            Message.send(
                conn,
                Response.error(
                    "definition too large (max %d bytes)" % WORKFLOW_MAX_FILE_SIZE
                ),
            )
            return
        try:
            definition = parse_definition(
                text, max_parallel_override=msg.get("max_parallel")
            )
        except definition_error as e:
            _logger.info("workflow 定义解析失败: %s", e)
            Message.send(conn, Response.error("workflow 定义错误: %s" % e))
            return

        vars_override = msg.get("vars_override")
        if vars_override is not None:
            if not isinstance(vars_override, dict):
                Message.send(conn, Response.error("vars_override must be a dict"))
                return
            for k, v in vars_override.items():
                if not isinstance(v, (str, int, float, bool)):
                    Message.send(
                        conn,
                        Response.error("变量 '%s' 的值类型不支持" % k),
                    )
                    return
        try:
            run = wm.start(definition, vars_override=vars_override)
        except ValueError as e:
            Message.send(conn, Response.error(str(e)))
            return
        Message.send(
            conn,
            {
                "type": "workflow",
                "action": "run",
                "runId": run.run_id,
                "status": "started",
            },
        )

    def _handle_show(self, wm, conn, msg: dict):
        run_id = msg.get("runId")
        if not run_id:
            Message.send(conn, Response.error("runId is required"))
            return
        run = wm.get_run(run_id)
        if run is None:
            Message.send(conn, Response.error("run not found: %s" % run_id))
            return
        Message.send(
            conn, {"type": "workflow", "action": "show", "run": run.snapshot()}
        )

    def _handle_cancel(self, wm, conn, msg: dict):
        run_id = msg.get("runId")
        if not run_id:
            Message.send(conn, Response.error("runId is required"))
            return
        if not wm.cancel_run(run_id):
            Message.send(conn, Response.error("run not found: %s" % run_id))
            return
        Message.send(
            conn,
            {"type": "workflow", "action": "cancel", "runId": run_id, "status": "cancelling"},
        )