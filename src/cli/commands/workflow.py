"""workflow 命令：workflow 脚本编排（run/list/show/cancel）"""

import argparse

from ..base import Command, CommandContext
from ..common_args import add_common_args


class WorkflowCommand(Command):
    """workflow 命令"""

    name = "workflow"
    help = "workflow 脚本编排（run/list/show/cancel）"
    # 公共参数已在各子子命令解析器手动注册（add_common_args），避免两级重复
    use_common_args = False

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        wf_sub = parser.add_subparsers(dest="workflow_subcmd", help="workflow 子命令")

        p_run = wf_sub.add_parser("run", help="启动 workflow（YAML 定义文件，后台执行）")
        add_common_args(p_run)
        p_run.add_argument("file", help="workflow 定义文件（YAML）")
        p_run.add_argument(
            "--vars",
            nargs="*",
            default=None,
            metavar="KEY=VALUE",
            help="覆盖定义中的全局变量（可指定多个）",
        )
        p_run.add_argument(
            "--parallel",
            type=int,
            default=None,
            metavar="N",
            help="最大并行步骤数（优先于定义中的 max_parallel）",
        )

        p_list = wf_sub.add_parser("list", help="列出所有 workflow 运行（含已结束）")
        add_common_args(p_list)

        p_show = wf_sub.add_parser("show", help="查看 workflow 运行状态（步骤+日志）")
        add_common_args(p_show)
        p_show.add_argument("run_id", metavar="RUN_ID", help="运行标识（run list 可见）")

        p_cancel = wf_sub.add_parser("cancel", help="取消 workflow 运行")
        add_common_args(p_cancel)
        p_cancel.add_argument("run_id", metavar="RUN_ID", help="运行标识")

    def run(self, args, ctx: CommandContext) -> None:
        if args.workflow_subcmd == "run":
            vars_overrides = None
            if args.vars:
                vars_overrides = {}
                for item in args.vars:
                    if "=" not in item:
                        ctx.parser.error(
                            f"--vars 参数必须为 KEY=VALUE 格式: {item}"
                        )
                    k, v = item.split("=", 1)
                    vars_overrides[k] = v
            ctx.client.cmd_workflow_run(
                args.file,
                vars_overrides=vars_overrides,
                max_parallel=args.parallel,
            )
        elif args.workflow_subcmd == "list":
            ctx.client.cmd_workflow_list()
        elif args.workflow_subcmd == "show":
            ctx.client.cmd_workflow_show(args.run_id)
        elif args.workflow_subcmd == "cancel":
            ctx.client.cmd_workflow_cancel(args.run_id)
        else:
            ctx.parser.print_help()