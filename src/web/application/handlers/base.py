"""WebSocket 消息用例处理器 —— 基类、上下文与共享工具。

每个处理器对应一种前端消息类型，负责执行业务逻辑并返回响应消息。
所有处理器只依赖应用端口和领域实体，不依赖具体框架或基础设施。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Optional

from ....logging import get_logger
from ..adaptive_lock import AdaptiveLockService
from ..ports import (
    ConnectionContext,
    CursorLocatorServicePort,
    EventPublisher,
    HistoryRepository,
    OutboundMessageChannel,
    SessionRepository,
    ShellProvider,
    SystemStatsProvider,
    ThreadExecutor,
)
from ..services import MessageEncoderService, SubscriptionService

if TYPE_CHECKING:
    from ....screenshare.ports import ScreenshareServicePort
    from ....vnc.ports import VncServicePort

_logger = get_logger("pty-web")


def _split_windows_command(cmd: str) -> list:
    """按 Windows CommandLineToArgvW 规则拆分字符串，正确处理引号与反斜杠转义。

    与 shlex.split(posix=False) 不同，本函数会去掉参数两端的引号，
    避免 PTY 模式下把引号当作路径的一部分传给子进程。
    反斜杠仅在其紧邻引号时参与转义（偶数个每对产生一个字面反斜杠，
    奇数个时剩余一个转义该引号），否则按字面保留。
    """
    args = []
    current = []
    in_quotes = False
    arg_started = False  # 当前参数是否已显式开始（含空引号），用于保留空参数
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\\":
            # 统计连续反斜杠
            backslashes = 0
            while i < n and cmd[i] == "\\":
                backslashes += 1
                i += 1
            if i < n and cmd[i] == '"':
                # 反斜杠紧邻引号：每对反斜杠产生一个字面反斜杠
                current.append("\\" * (backslashes // 2))
                if backslashes % 2 == 1:
                    # 奇数个：剩余一个转义该引号，引号作为字面量写入
                    current.append('"')
                    i += 1
                else:
                    # 偶数个：引号作为普通分隔符
                    in_quotes = not in_quotes
                    i += 1
            else:
                current.append("\\" * backslashes)
            arg_started = True
        elif c == '"':
            # 引号内连续双引号表示一个字面引号；引号外连续双引号则成对开闭（空串）
            if in_quotes and i + 1 < n and cmd[i + 1] == '"':
                current.append('"')
                i += 2
                continue
            in_quotes = not in_quotes
            arg_started = True
            i += 1
        elif c in (" ", "\t") and not in_quotes:
            if arg_started:
                args.append("".join(current))
                current = []
                arg_started = False
            i += 1
        else:
            current.append(c)
            arg_started = True
            i += 1
    if arg_started:
        args.append("".join(current))
    return args


def _command_to_string(command) -> str:
    """将命令转换为可显示的字符串。"""
    return command if isinstance(command, str) else " ".join(command)


def _resolve_session_uid(ctx, msg: dict) -> str:
    """从消息解析会话 uid：优先 sessionUid 字段，否则按 sid 经 resolve_sid 转换。

    uid 是会话唯一稳定标识（sid 可复用）；后端所有路由/订阅/锁均以 uid 为准，
    保证同名 sid 会话（先后复用）之间不串扰。解析失败返回空串。
    """
    uid = msg.get("sessionUid", "") or ""
    if uid:
        return uid
    sid = msg.get("session_id", "")
    if not sid:
        return ""
    try:
        resolved = ctx.session_repo.resolve_sid(sid)
        return resolved or ""
    except Exception:
        return ""


class HandlerContext:
    """处理器共享的上下文对象。"""

    def __init__(
        self,
        session_repo: SessionRepository,
        history_repo: Optional[HistoryRepository],
        system_stats: SystemStatsProvider,
        shell_provider: ShellProvider,
        executor: ThreadExecutor,
        encoder: MessageEncoderService,
        subscription: SubscriptionService,
        publisher: EventPublisher,
        connection: ConnectionContext,
        channel: OutboundMessageChannel,
        enqueue: Callable[[dict], None],
        adaptive_lock: Optional[AdaptiveLockService] = None,
        vnc_service: Optional[VncServicePort] = None,
        screenshare_service: Optional[ScreenshareServicePort] = None,
        cursor_locator_service: Optional[CursorLocatorServicePort] = None,
    ):
        self.session_repo = session_repo
        self.history_repo = history_repo
        self.system_stats = system_stats
        self.shell_provider = shell_provider
        self.executor = executor
        self.encoder = encoder
        self.subscription = subscription
        self.publisher = publisher
        self.connection = connection
        self.channel = channel
        self.enqueue = enqueue
        self.adaptive_lock = adaptive_lock
        self.vnc_service = vnc_service
        self.screenshare_service = screenshare_service
        self.cursor_locator_service = cursor_locator_service


class MessageHandler(ABC):
    """消息处理器抽象基类。"""

    @abstractmethod
    async def handle(self, ctx: HandlerContext, msg: dict) -> list[dict]:
        """处理消息并返回响应消息列表。"""