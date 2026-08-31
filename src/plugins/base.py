"""插件协议 — Plugin 基类与运行时上下文

插件元数据（id/version/kind/triggers/messageTypes/权限/配置默认值等）全部由
plugin.json 清单声明（见 manifest.py），本类只定义钩子签名与上下文能力。
声明即契约：清单声明的触发方式/钩子必须在类中实现，加载期校验失败跳过插件。

生命周期约定（按形态）：
- process：注册表 enable 时构造单例 → on_init → on_enable；disable → on_disable
- session：注册表 enable 时构造规范实例（on_init → on_enable，收总线事件）；
  每次会话挂载构造独立实例（on_init → on_attach）；卸载 → on_detach
- cli：客户端进程加载时构造 → on_init（on_enable/on_disable 不参与）

钩子类型（调度语义，见 hooks.py）：
- modify（on_input/on_output/on_snapshot）：链式变换，返回 None 拦截
- observe（on_event/on_poll/on_bus_event/生命周期）：只通知
- provide（inspect_state/handle_command）：首个非 None 生效
- CLI（before_request/transform_response/render_response）：客户端侧三阶段
"""

from types import MappingProxyType
from typing import Optional

from ..logging import get_logger

_logger = get_logger("pty-plugins")

# 空选项共享常量：只读映射，插件对 ctx.options 只能读；
# 误写会立即抛 TypeError（而非静默污染共享空 dict）
_EMPTY_OPTIONS: MappingProxyType = MappingProxyType({})

# handle_message 返回值哨兵：插件已自行完成多帧响应，调度器不再发送
HANDLED = object()

# 合法钩子名全集（manifest.hooks 可声明的键；实现即生效）
VALID_HOOKS = (
    "on_init", "on_enable", "on_disable",
    "on_attach", "on_detach",
    "on_input", "on_output", "on_snapshot",
    "on_event", "on_poll", "on_bus_event",
    "handle_command", "handle_message", "inspect_state", "decorate_response",
    "check_request", "before_request", "transform_response", "render_response",
    "on_session_created",
)


class Plugin:
    """插件基类 —— 只实现钩子，元信息来自 plugin.json

    实例属性（加载器注入）：
        name/version/description/kind: 清单同名字段（name 即插件 id）
        manifest: PluginManifest 引用
    """

    name: str = ""
    version: str = ""
    description: str = ""
    kind: str = ""
    manifest = None

    # ── 生命周期 ──────────────────────────────────────────

    def on_init(self, ctx) -> None:
        """实例构造后初始化（配置/存储/日志准备）"""

    def on_enable(self, ctx) -> None:
        """全局启用（process/session 形态：注册表 enable 时由规范实例回调）"""

    def on_disable(self, ctx) -> None:
        """全局停用（process/session 形态：注册表 disable 时由规范实例回调）"""

    # ── 会话挂载 ──────────────────────────────────────────

    def on_attach(self, ctx) -> None:
        """挂载到会话时调用（exec 注入或动态 attach；会话可能尚未启动）"""

    def on_detach(self, ctx, exit_code) -> None:
        """从会话卸载时调用（用户 detach、自我卸载或会话结束）"""

    def on_session_created(self, ctx, session, msg) -> None:
        """ExecHandler 创建会话成功后回调（进程级插件；会话附加标记/启动监控）

        在通用 exec 流程（check_ended_session → create_session）之后调用，
        插件在此标记会话归属、启动会话级监控等，不接管 exec 处理。
        """

    # ── 变换链（modify，链式，返回 None 的输入拦截）────────

    def on_input(self, ctx, data):
        """PTY 写入前的输入变换；返回 None 表示拦截丢弃"""
        return data

    def on_output(self, ctx, data: bytes) -> bytes:
        """PTY 读到的原始输出变换（reader 线程；贯穿 buffer/快照/推送）"""
        return data

    def on_snapshot(self, ctx, text: str) -> str:
        """终端快照文本变换"""
        return text

    # ── 触发（observe，需清单声明）────────────────────────

    def on_event(self, ctx, event: dict) -> None:
        """会话事件订阅（需清单 triggers 含 "event"）"""

    def on_poll(self, ctx) -> None:
        """定时触发回调（需清单 triggers 含 "poll" + pollInterval）"""

    def on_bus_event(self, ctx, event) -> None:
        """daemon 事件总线事件（按清单 events.subscribe 模式订阅）"""

    # ── 命令（provide，首个非 None 生效）──────────────────

    def handle_command(self, ctx, msg: dict):
        """自定义命令处理（plugin cmd 触发）；未处理返回 None"""

    def handle_message(self, ctx, msg: dict):
        """进程级命令处理（需清单 messageTypes 非空）

        返回 dict 原样作为响应发送；返回 HANDLED 表示已自行完成多帧响应；
        返回 None 表示未处理。
        """

    def inspect_state(self, ctx):
        """命令返回时的一次性状态检查；返回 dict 附加为 terminalState"""

    def decorate_response(self, ctx, resp: dict):
        """装饰内置命令的响应（按 manifest.decorateTypes 匹配 commandType）
        
        返回修改后的 resp dict，或 None 表示不修改。
        """

    # ── CLI 三阶段（kind=cli）─────────────────────────────

    def check_request(self, ctx, msg: dict) -> Optional[str]:
        """请求发送前拦截检查；返回 None 放行，返回 str 拒绝（该字符串作为错误消息）"""

    def before_request(self, ctx, msg: dict):
        """请求发送前调用；返回 dict 替换 msg，None 放行"""

    def transform_response(self, ctx, resp: dict):
        """响应收到后调用；返回 dict 替换 resp，None 不变"""

    def render_response(self, ctx, resp: dict):
        """响应打印前调用；返回 str 直接打印，None 走默认 JSON"""


class _EnvContext:
    """上下文公共能力 — 事件总线/配置/存储/权限/日志/通知（daemon 全局共享）"""

    __slots__ = ("events", "notify_manager", "config", "storage", "permission", "logger")

    def __init__(self, environment, plugin):
        if environment is not None:
            self.events = environment.events
            self.notify_manager = getattr(environment, "notify_manager", None)
            self.config = environment.config_for(plugin.name)
            self.storage = environment.storage_for(plugin.name)
            self.permission = environment.permission_for(plugin.name)
            self.logger = environment.logger_for(plugin.name)
        else:
            self.events = None
            self.notify_manager = None
            self.config = None
            self.storage = None
            self.permission = None
            self.logger = _logger


class PluginContext(_EnvContext):
    """会话级插件运行时上下文（每个钩子调用由宿主构造）

    options: 本插件的会话选项（cliOptions 声明，exec 时注入、send/read/mouse
    更新）；会话生命周期内所有钩子可读，未设置时为空 dict。
    """

    __slots__ = ("_host", "plugin", "session", "options")

    def __init__(self, session, plugin, host=None, environment=None, options=None):
        env = host.environment if host is not None else environment
        super().__init__(env, plugin)
        self.session = session
        self.plugin = plugin
        self._host = host
        # None 时用空 dict 常量（插件只读 options，共享空 dict 安全）；
        # 非 None 原样引用（宿主已按值拷贝或传入共享空常量）
        self.options = options if options is not None else _EMPTY_OPTIONS

    def request_return(self, reason: str) -> bool:
        """请求当前等待命令（exec/send 的 trigger/snapshot 等待）立即返回

        原因字符串原样透传给调用方（triggerReturnReason）。
        仅当会话正有等待循环且未执行时生效；无等待时请求被丢弃并返回 False。
        """
        if self._host is None:
            return False
        return self._host.request_return(reason)

    def self_unload(self) -> bool:
        """请求从当前会话卸载自身（当前钩子链结束后生效，会触发 on_detach）

        已卸载或不在链上时返回 False。
        """
        if self._host is None:
            return False
        return self._host.self_unload(self.plugin)


class ProcessPluginContext(_EnvContext):
    """进程级插件运行时上下文（每个消息处理由调度器构造）"""

    __slots__ = ("io", "manager", "plugin")

    def __init__(self, manager, plugin, io, environment=None):
        super().__init__(environment, plugin)
        self.manager = manager
        self.plugin = plugin
        self.io = io