"""客户端 workflow 命令混入 —— workflow run/list/show/cancel。

workflow 命令职责（ClientWorkflowCommandsMixin）。
run 由 client 侧读取 YAML 定义文件，发送 daemon 解析执行。
"""

from typing import Optional

from ..protocol.response import Response
from ..logging import get_logger
from . import presenter

_logger = get_logger("pty-client")


class ClientWorkflowCommandsMixin:
    """workflow 脚本编排命令域"""

    def cmd_workflow_run(
        self,
        file_path: str,
        vars_overrides: Optional[dict] = None,
        max_parallel: Optional[int] = None,
    ):
        """启动 workflow（YAML 定义文件，client 侧读取后发送 daemon 解析执行）"""
        _logger.info(
            "cmd_workflow_run: file=%r vars=%s max_parallel=%s",
            file_path,
            vars_overrides,
            max_parallel,
        )
        if not file_path:
            presenter.print_response(Response.error("workflow file path is required"))
            return
        try:
            with open(file_path, "rb") as f:
                data = f.read()
        except OSError as e:
            presenter.print_response(Response.error("读取 workflow 文件失败: %s" % e))
            return
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            presenter.print_response(Response.error("workflow 文件不是合法 UTF-8: %s" % e))
            return
        msg: dict = {"type": "workflow", "action": "run", "definition": text}
        if vars_overrides:
            msg["vars_override"] = vars_overrides
        if max_parallel is not None:
            msg["max_parallel"] = max_parallel
        resp = self._send_recv(msg, autostart=True)
        presenter.print_response(resp)

    def cmd_workflow_list(self):
        """列出所有 workflow 运行（含已结束）"""
        _logger.info("cmd_workflow_list")
        resp = self._send_recv({"type": "workflow", "action": "list"}, autostart=True)
        presenter.print_response(resp)

    def cmd_workflow_show(self, run_id: str):
        """查看单次 workflow 运行状态（步骤状态 + 日志）"""
        _logger.info("cmd_workflow_show: run_id=%r", run_id)
        if not run_id:
            presenter.print_response(Response.error("runId is required"))
            return
        resp = self._send_recv(
            {"type": "workflow", "action": "show", "runId": run_id}, autostart=True
        )
        presenter.print_response(resp)

    def cmd_workflow_cancel(self, run_id: str):
        """请求取消 workflow 运行"""
        _logger.info("cmd_workflow_cancel: run_id=%r", run_id)
        if not run_id:
            presenter.print_response(Response.error("runId is required"))
            return
        resp = self._send_recv(
            {"type": "workflow", "action": "cancel", "runId": run_id}, autostart=True
        )
        presenter.print_response(resp)