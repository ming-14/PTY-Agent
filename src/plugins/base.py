"""插件协议 — Plugin 基类与 PluginContext

插件通过继承 Plugin 声明钩子与触发方式，挂载到会话后由 PluginHost 调度。
所有钩子统一接收 PluginContext（含会话引用与返回控制/自我卸载能力）。

触发方式（triggers）统一声明：
- "event": 事件触发，宿主在会话事件（进程 spawn/exit/crash、GUI 窗口）产生时调用 on_event
- "poll":  定时触发，宿主在监控循环按 poll_interval 节流调用 on_poll
声明即契约：triggers 含非法值、声明 "poll" 缺 poll_interval、
声明触发方式却未实现对应方法等，均在加载期校验失败并跳过该插件。
"""

import logging
from typing import List, Optional

_logger = logging.getLogger("pty-plugins")

# 合法触发方式
VALID_TRIGGERS = ("event", "poll")

# handle_message 返回值哨兵：插件已自行完成响应（多帧协议，如 upload/download），
# 调度器不再发送任何消息
HANDLED = object()


class Plugin:
    """插件基类

    子类需声明：
        name:           唯一插件名（默认取模块文件名）。
        version:        版本号。
        description:    功能描述。
        triggers:       触发方式列表（"event"/"poll"/两者）。
        poll_interval:  "poll" 触发的轮询间隔（秒，声明 poll 时必填）。
        auto_load:      自动加载条件（exec 时按 command/cwd/env 匹配，命中自动挂载）。

    钩子（宿主自动在正确时机调用，实现即生效）：
        on_attach / on_detach: 挂载/卸载生命周期（detach 含会话结束）。
        on_input / on_output / on_snapshot: 输入/输出/快照变换链。
        on_event:   事件订阅（需声明 "event"）。
        on_poll:    定时回调（需声明 "poll" + poll_interval）。
        handle_command: 自定义命令（外部经 plugin cmd 触发，未处理返回 None）。

    进程级插件（message_types 非空 = 进程级）：
        message_types: 声明要接管的 daemon 消息类型列表（如 ["file_read"]）；
                      非空时插件为进程级：守护进程启动时单例实例化，
                      对应消息类型由 dispatcher 路由到 handle_message。
        needs_io:      进程级插件是否需要连接 I/O 通道（多轮帧协议用，
                      如 file upload/download；session 级插件忽略）。
        handle_message(ctx, msg): 进程级命令处理；返回 dict 原样作为响应
                      发送（保持既有消息协议契约），返回 None 表示未处理。

    约束：
        - on_output 在 reader 线程被调用，禁止慢操作/阻塞。
        - 钩子可能被多个线程并发调用，实现需保证线程安全。
        - 插件在 daemon 进程内执行，权限等同 daemon，仅应挂载可信代码。
    """

    name: str = ""
    version: str = "1.0"
    description: str = ""
    triggers: List[str] = ["event"]
    poll_interval: Optional[float] = None
    auto_load: Optional[dict] = None
    message_types: List[str] = []
    needs_io: bool = False

    def on_attach(self, ctx) -> None:
        """挂载到会话时调用（exec 注入或动态 attach；会话可能尚未启动）"""

    def on_detach(self, ctx, exit_code) -> None:
        """从会话卸载时调用（用户 detach、自我卸载或会话结束）"""

    def on_input(self, ctx, data):
        """PTY 写入前的输入变换；返回 None 表示拦截丢弃"""
        return data

    def on_output(self, ctx, data: bytes) -> bytes:
        """PTY 读到的原始输出变换（reader 线程；变换结果贯穿 buffer/快照/推送）"""
        return data

    def on_snapshot(self, ctx, text: str) -> str:
        """终端快照文本变换（handler 线程；变换结果返回给调用方）"""
        return text

    def on_event(self, ctx, event: dict) -> None:
        """会话事件订阅（需声明 "event"）"""

    def on_poll(self, ctx) -> None:
        """定时触发回调（需声明 "poll" + poll_interval）"""

    def handle_command(self, ctx, msg: dict):
        """自定义命令处理；未处理返回 None（宿主按未处理响应错误）"""
        return

    def handle_message(self, ctx, msg: dict):
        """进程级命令处理（需声明 message_types）

        返回 dict 原样作为响应发送（响应签名由框架层完成），
        返回 None 表示未处理（宿主按未处理响应错误）。
        """
        return

    def inspect_state(self, ctx):
        """返回时状态检查钩子（可选）：命令响应构造时被调用一次

        返回 dict 将作为 terminalState 附加到命令响应（exec/send/read/mouse），
        返回 None 表示不提供状态。适用于一次性检查当前终端状态并随响应返回。
        """
        return


class PluginContext:
    """插件运行时上下文 — 每个钩子调用时由宿主构造

    Attributes:
        session: 当前会话引用（含 get_output/get_snapshot/write_input 等公开 API）。
        plugin:  插件实例自身。
    """

    __slots__ = ("_host", "plugin", "session")

    def __init__(self, session, plugin, host):
        self.session = session
        self.plugin = plugin
        self._host = host

    def request_return(self, reason: str) -> bool:
        """请求当前等待命令（exec/send 的 trigger/snapshot 等待）立即返回

        原因字符串原样透传给调用方（triggerReturnReason）。
        仅当会话正有等待循环时生效；无等待时请求被丢弃并返回 False。
        """
        return self._host.request_return(reason)

    def self_unload(self) -> bool:
        """请求从当前会话卸载自身（当前钩子链结束后生效，会触发 on_detach）

        已卸载或不在链上时返回 False。
        """
        return self._host.self_unload(self.plugin)


class ProcessPluginContext:
    """进程级插件运行时上下文 — 每个消息处理时由调度器构造

    Attributes:
        manager: 会话管理器（会话 cwd 解析等）。
        plugin:  插件实例自身。
        io:      连接 I/O 通道（PluginIO）；插件声明 needs_io 时可用，否则 None。
    """

    __slots__ = ("io", "manager", "plugin")

    def __init__(self, manager, plugin, io):
        self.manager = manager
        self.plugin = plugin
        self.io = io
