"""start 命令：启动后台守护进程"""

import os
import sys

from ...client.presenter import emit
from ..base import Command, CommandContext

# 项目根（src/cli/commands/start.py → 上三级）；exec 前台 daemon 时作为 PYTHONPATH
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


class StartCommand(Command):
    """start 命令"""

    name = "start"
    help = "启动后台守护进程"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--foreground",
            action="store_true",
            help="前台运行（供 s6/systemd 等服务监督器管理；不双 fork，"
            "当前进程 exec 替换为 daemon，日志输出到 stderr）",
        )
        parser.add_argument(
            "--survive",
            action="store_true",
            help="生存模式：运行期间拦截忽略所有结束进程的信号"
            "（SIGTERM/SIGHUP/SIGINT/SIGQUIT）与 stop 协议消息，"
            "仅 SIGKILL 可终止（stop --force 仍可用）",
        )

    def run(self, args, ctx: CommandContext) -> None:
        # --encoding 的取值校验已在 argparse 解析期完成（非法值直接报错退出，
        # 与守护进程是否已运行无关）。该参数仅作为本次调用的取值白名单校验，
        # 并无传播路径：不参与守护进程启动，也不影响已运行守护进程的会话默认
        # 编码（会话默认编码请用 --default encoding / set-default）。
        encoding = getattr(args, "encoding", None)
        if encoding:
            emit(
                f"--encoding {encoding!r} 取值已校验，但不影响守护进程启动"
                "（会话默认编码请使用 --default encoding / set-default）"
            )
        # 生存模式经环境变量传递（exec/子进程双路径均继承）
        if getattr(args, "survive", False):
            os.environ["PTY_AGENT_SURVIVE"] = "1"
        if getattr(args, "foreground", False):
            self._exec_foreground()
        ctx.client.cmd_start()

    def _exec_foreground(self) -> None:
        """前台模式：当前进程 exec 替换为 daemon 前台进程

        与 `python -m src.daemon --foreground` 等价但走 CLI 入口，供 s6 等
        监督器以 `exec python app.py start --foreground` 直接持有 daemon 进程
        ——exec 链保持同一 PID，监督器即可监控/保活/捕获 stderr 日志。
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = _PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        os.execvpe(
            sys.executable,
            [sys.executable, "-m", "src.daemon", "--foreground"],
            env,
        )
        # execvpe 成功不返回；失败时抛 OSError 由顶层统一兜底
