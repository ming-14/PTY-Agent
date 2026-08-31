"""会话级插件宿主 — 挂载链与钩子调度（HookEngine 驱动）

持有当前会话挂载的插件实例链（挂载顺序即链顺序），提供：
- 链式变换钩子：on_input / on_output / on_snapshot（modify，任一返回 None 的输入被拦截）
- 分发钩子：    on_event（事件订阅）/ poll_tick（定时轮询，按 pollInterval 节流）
- 生命周期：    attach / detach / detach_all（attach 依次回调 on_init/on_attach）
- 插件选项：    attach 注入 / update_options 合并，会话生命周期内所有钩子经
                ctx.options 可读（cliOptions 声明，exec/send/read/mouse 下发）
- 返回控制：    request_return（中断等待中的命令，原因透传）
- 自我卸载：    self_unload（标记待卸载，当前钩子链结束后移除并触发 on_detach）
- 总线发布：    publish_event（会话事件发布到 daemon 事件总线）

所有插件调用统一异常隔离：插件异常只记日志，不中断主流程。

并发约束：on_output 在 reader 线程、poll_tick 在监控线程、其余在 handler 线程
被调用，宿主不做额外加锁，插件实现需保证自身线程安全。
"""

import threading
import time
from typing import Dict, List, Optional

from .base import Plugin, PluginContext, _EMPTY_OPTIONS
from .hooks import HookEngine
from ..logging import get_logger

_logger = get_logger("pty-plugins")


class PluginHost:
    """会话级插件宿主（空链时所有调用零开销短路）"""

    def __init__(self, session, environment=None, plugins=None):
        self._session = session
        self.environment = environment
        self._plugins: List[Plugin] = []
        self._options: Dict[str, dict] = {}   # 插件名 → 会话选项（cliOptions 下发）
        self._lock = threading.Lock()
        self._pending_unload: List[Plugin] = []
        # 待卸载标记：self_unload 置位，_flush_unload 先查 Event 免每块取锁
        self._unload_pending = threading.Event()
        self._return_request: Optional[str] = None
        self._wait_active: bool = False
        self._last_poll: Dict[Plugin, float] = {}
        self._engine = HookEngine()
        if plugins:
            for plugin in plugins:
                self.attach(plugin)

    # ── 查询 ──────────────────────────────────────────────

    def is_empty(self) -> bool:
        return not self._plugins

    def names(self) -> List[str]:
        return [p.name for p in self._plugins]

    def get(self, name: str) -> Optional[Plugin]:
        for p in self._plugins:
            if p.name == name:
                return p
        return None

    def snapshot_info(self) -> Optional[List[dict]]:
        """挂载插件信息快照（name/version/options）；空链返回 None"""
        if not self._plugins:
            return None
        with self._lock:
            return [
                {
                    "name": p.name,
                    "version": p.version,
                    "options": dict(self._options.get(p.name, {})),
                }
                for p in self._plugins
            ]

    # ── 挂载/卸载 ─────────────────────────────────────────

    def attach(self, plugin: Plugin, options=None) -> bool:
        """挂载插件（同名去重）并回调 on_init/on_attach；重名返回 False

        options: 本次挂载的插件选项（cliOptions 下发）；None 时沿用
        会话已存选项（动态 attach 场景，exec 时设置的选项继续生效）。
        """
        with self._lock:
            if any(p.name == plugin.name for p in self._plugins):
                _logger.warning(
                    "插件 %s 已挂载到会话 %s，跳过", plugin.name, self._session.id
                )
                return False
            self._plugins.append(plugin)
            if options is not None:
                self._options[plugin.name] = dict(options)
        self._engine.register(plugin)
        _logger.info("插件挂载: %s -> 会话 %s", plugin.name, self._session.id)
        self._safe_call(plugin, "on_init", self._ctx(plugin))
        self._safe_call(plugin, "on_attach", self._ctx(plugin))
        return True

    def update_options(self, options: dict) -> None:
        """合并会话插件选项（send/read/mouse 消息下发；逐插件合并）

        对已挂载与未挂载插件均生效：未挂载插件的选项保留，后续动态
        attach 时自动沿用。
        """
        with self._lock:
            for name, opts in (options or {}).items():
                if not isinstance(opts, dict):
                    continue
                merged = dict(self._options.get(name, {}))
                merged.update(opts)
                self._options[name] = merged
        if options:
            _logger.info(
                "插件选项合并: 会话 %s 插件数=%d", self._session.id, len(options)
            )

    def options_for(self, name: str) -> dict:
        """读取插件会话选项（未设置返回空 dict）"""
        with self._lock:
            return dict(self._options.get(name, {}))

    def detach(self, plugin_name: str, exit_code=None) -> bool:
        """按名卸载插件并回调 on_detach；未挂载返回 False"""
        with self._lock:
            inst = self.get(plugin_name)
            if inst is None:
                return False
            self._plugins.remove(inst)
        self._engine.unregister(inst)
        _logger.info("插件卸载: %s <- 会话 %s", plugin_name, self._session.id)
        self._safe_call(
            inst, "on_detach", self._ctx(inst), exit_code
        )
        return True

    def detach_all(self, exit_code=None) -> None:
        """卸载全部插件（会话结束时调用，幂等）"""
        with self._lock:
            plugins = list(self._plugins)
            self._plugins.clear()
        for inst in plugins:
            _logger.info(
                "插件卸载(会话结束): %s <- 会话 %s", inst.name, self._session.id
            )
            self._engine.unregister(inst)
            self._safe_call(
                inst, "on_detach", self._ctx(inst), exit_code
            )

    def self_unload(self, plugin: Plugin) -> bool:
        """插件自我卸载请求：仅标记，当前钩子链结束后统一移除"""
        with self._lock:
            if plugin in self._plugins and plugin not in self._pending_unload:
                self._pending_unload.append(plugin)
                self._unload_pending.set()
                return True
            return False

    def _flush_unload(self) -> None:
        """处理待卸载标记：链结束后调用（锁外执行避免重入）

        惰性检查 Event：无待卸载标记时直接返回，避免每块输出取锁。
        """
        if not self._unload_pending.is_set():
            return
        self._unload_pending.clear()
        with self._lock:
            pending = list(self._pending_unload)
            self._pending_unload.clear()
        for plugin in pending:
            _logger.info("插件自我卸载: %s <- 会话 %s", plugin.name, self._session.id)
            self.detach(plugin.name)

    # ── 返回控制（等待循环） ──────────────────────────────

    def enter_wait(self) -> None:
        """等待循环开始（exec/send 的 trigger/snapshot 等待）"""
        with self._lock:
            self._wait_active = True

    def exit_wait(self) -> None:
        """等待循环结束：注销等待并清除残留请求"""
        with self._lock:
            self._wait_active = False
            self._return_request = None

    def request_return(self, reason: str) -> bool:
        """请求当前等待命令立即返回（仅等待激活时生效）"""
        with self._lock:
            if not self._wait_active:
                _logger.debug("request_return 无等待循环，丢弃: %r", reason)
                return False
            self._return_request = reason
            _logger.info("插件请求返回: 会话 %s reason=%r", self._session.id, reason)
            return True

    def consume_return_request(self) -> Optional[str]:
        """等待循环消费返回请求（消费后清除）"""
        with self._lock:
            req = self._return_request
            self._return_request = None
            return req

    # ── 钩子调度（HookEngine 驱动） ───────────────────────

    def _ctx(self, plugin: Plugin) -> PluginContext:
        opts = self._options.get(plugin.name)
        return PluginContext(
            self._session, plugin, self,
            options=dict(opts) if opts else _EMPTY_OPTIONS,
        )

    def on_input(self, data):
        """PTY 写入前链式变换；任一插件返回 None 表示拦截（返回 None）"""
        result = self._engine.dispatch_modify("on_input", self._ctx, data)
        self._flush_unload()
        return result

    def on_output(self, data: bytes) -> bytes:
        """reader 线程调用：链式变换 PTY 原始输出；插件返回 None 视为不修改"""
        result = self._engine.dispatch_modify("on_output", self._ctx, data, intercept=False)
        self._flush_unload()
        return data if result is None else result

    def on_snapshot(self, text: str) -> str:
        """快照文本链式变换（handler 线程）；插件返回 None 视为不修改"""
        result = self._engine.dispatch_modify("on_snapshot", self._ctx, text, intercept=False)
        self._flush_unload()
        return text if result is None else result

    def on_event(self, event: dict) -> None:
        """事件分发：仅通知声明了 "event" 触发的插件（引擎注册时已门控）"""
        self._engine.dispatch_observe("on_event", self._ctx, event)
        self._flush_unload()

    def poll_tick(self) -> None:
        """监控循环每轮调用：按各插件 poll_interval 节流触发 on_poll"""
        now = time.monotonic()
        with self._lock:
            stale = [p for p in self._last_poll if p not in self._plugins]
            for p in stale:
                self._last_poll.pop(p, None)
        plugins = list(self._plugins)
        for plugin in plugins:
            manifest = getattr(plugin, "manifest", None)
            if manifest is None or "poll" not in manifest.triggers:
                continue
            interval = manifest.poll_interval or 0.0
            with self._lock:
                last = self._last_poll.get(plugin, 0.0)
                if now - last < interval:
                    continue
                self._last_poll[plugin] = now
            self._safe_call(plugin, "on_poll", self._ctx(plugin))
        self._flush_unload()

    def handle_command(self, plugin_name: str, msg: dict):
        """路由自定义命令到指定插件；未挂载或未处理返回 None"""
        with self._lock:
            inst = self.get(plugin_name)
        if inst is None:
            return None
        try:
            return inst.handle_command(self._ctx(inst), msg)
        except Exception:
            _logger.exception(
                "插件 %s handle_command 异常 (会话 %s)", plugin_name, self._session.id
            )
            return None

    def inspect_state(self) -> Optional[dict]:
        """命令返回时的一次性状态检查（provide：首个非 None 生效）"""
        return self._engine.dispatch_provide("inspect_state", self._ctx)

    def publish_event(self, topic: str, payload: dict) -> None:
        """发布会话事件到 daemon 事件总线（无环境时静默跳过）"""
        env = self.environment
        if env is None:
            return
        try:
            env.events.publish(topic, payload, source="session." + self._session.id)
        except Exception:
            _logger.exception("会话事件发布失败: %s", topic)

    # ── 工具 ──────────────────────────────────────────────

    def _safe_call(self, plugin: Plugin, method_name: str, *args) -> None:
        try:
            getattr(plugin, method_name)(*args)
        except Exception:
            _logger.exception(
                "插件 %s %s 异常 (会话 %s)", plugin.name, method_name, self._session.id
            )