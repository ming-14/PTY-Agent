"""会话级插件宿主 — 挂载链与钩子调度

持有当前会话挂载的插件实例链（挂载顺序即链顺序），提供：
- 链式变换钩子：on_input / on_output / on_snapshot（管道式，任一返回 None 的输入被拦截）
- 分发钩子：    on_event（事件订阅）/ poll_tick（定时轮询，按各插件 poll_interval 节流）
- 生命周期：    attach / detach / detach_all
- 返回控制：    request_return（中断等待中的命令，原因透传）
- 自我卸载：    self_unload（标记待卸载，当前钩子链结束后移除并触发 on_detach）

所有插件调用统一异常隔离：插件异常只记日志，不中断主流程。

并发约束：on_output 在 reader 线程、poll_tick 在监控线程、其余在 handler 线程
被调用，宿主不做额外加锁，插件实现需保证自身线程安全。
"""

import threading
import time
from typing import Dict, List, Optional

from .base import Plugin, PluginContext
from ..logging import get_logger

_logger = get_logger("pty-plugins")


class PluginHost:
    """会话级插件宿主（空链时所有调用零开销短路）"""

    def __init__(self, session, plugins: Optional[List[Plugin]] = None):
        self._session = session
        self._plugins: List[Plugin] = []
        self._lock = threading.Lock()
        self._pending_unload: List[Plugin] = []
        self._return_request: Optional[str] = None
        self._wait_active: bool = False
        self._last_poll: Dict[Plugin, float] = {}
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
        """挂载插件信息快照（name/version）；空链返回 None"""
        if not self._plugins:
            return None
        return [{"name": p.name, "version": p.version} for p in self._plugins]

    # ── 挂载/卸载 ─────────────────────────────────────────

    def attach(self, plugin: Plugin, session=None) -> bool:
        """挂载插件（同名去重）并调用 on_attach；重名返回 False"""
        with self._lock:
            if any(p.name == plugin.name for p in self._plugins):
                _logger.warning(
                    "插件 %s 已挂载到会话 %s，跳过", plugin.name, self._session.id
                )
                return False
            self._plugins.append(plugin)
        _logger.info("插件挂载: %s -> 会话 %s", plugin.name, self._session.id)
        self._safe_call(plugin, "on_attach", PluginContext(self._session, plugin, self))
        return True

    def attach_many(self, plugins: List[Plugin]) -> List[str]:
        """批量挂载，返回实际挂载成功的插件名列表"""
        mounted = []
        for plugin in plugins:
            if self.attach(plugin):
                mounted.append(plugin.name)
        return mounted

    def detach(self, plugin_name: str, exit_code=None) -> bool:
        """按名卸载插件并调用 on_detach；未挂载返回 False"""
        with self._lock:
            inst = self.get(plugin_name)
            if inst is None:
                return False
            self._plugins.remove(inst)
        _logger.info("插件卸载: %s <- 会话 %s", plugin_name, self._session.id)
        self._safe_call(
            inst, "on_detach", PluginContext(self._session, inst, self), exit_code
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
            self._safe_call(
                inst, "on_detach", PluginContext(self._session, inst, self), exit_code
            )

    def self_unload(self, plugin: Plugin) -> bool:
        """插件自我卸载请求：仅标记，当前钩子链结束后统一移除"""
        with self._lock:
            if plugin in self._plugins and plugin not in self._pending_unload:
                self._pending_unload.append(plugin)
                return True
            return False

    def _flush_unload(self) -> None:
        """处理待卸载标记：链结束后调用（锁外执行避免重入）"""
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

    # ── 钩子调度 ──────────────────────────────────────────

    def on_input(self, data):
        """PTY 写入前链式变换；任一插件返回 None 表示拦截（返回 None）"""
        for plugin in list(self._plugins):
            try:
                result = plugin.on_input(
                    PluginContext(self._session, plugin, self), data
                )
            except Exception:
                _logger.exception("插件 %s on_input 异常", plugin.name)
                continue
            if result is None:
                _logger.info(
                    "插件 %s 拦截输入 (会话 %s)", plugin.name, self._session.id
                )
                self._flush_unload()
                return None
            data = result
        self._flush_unload()
        return data

    def on_output(self, data: bytes) -> bytes:
        """reader 线程调用：链式变换 PTY 原始输出"""
        for plugin in list(self._plugins):
            try:
                data = plugin.on_output(
                    PluginContext(self._session, plugin, self), data
                )
                if data is None:
                    data = b""
            except Exception:
                _logger.exception("插件 %s on_output 异常", plugin.name)
        self._flush_unload()
        return data

    def on_snapshot(self, text: str) -> str:
        """快照文本链式变换（handler 线程）"""
        for plugin in list(self._plugins):
            try:
                text = plugin.on_snapshot(
                    PluginContext(self._session, plugin, self), text
                )
                if text is None:
                    text = ""
            except Exception:
                _logger.exception("插件 %s on_snapshot 异常", plugin.name)
        self._flush_unload()
        return text

    def on_event(self, event: dict) -> None:
        """事件分发：仅通知声明了 "event" 触发的插件"""
        session = self._session
        for plugin in list(self._plugins):
            if "event" not in plugin.triggers:
                continue
            self._safe_call(
                plugin, "on_event", PluginContext(session, plugin, self), event
            )
        self._flush_unload()

    def poll_tick(self) -> None:
        """监控循环每轮调用：按各插件 poll_interval 节流触发 on_poll"""
        now = time.monotonic()
        with self._lock:
            stale = [p for p in self._last_poll if p not in self._plugins]
            for p in stale:
                self._last_poll.pop(p, None)
        for plugin in list(self._plugins):
            if "poll" not in plugin.triggers:
                continue
            with self._lock:
                last = self._last_poll.get(plugin, 0.0)
                if now - last < plugin.poll_interval:
                    continue
                self._last_poll[plugin] = now
            self._safe_call(
                plugin, "on_poll", PluginContext(self._session, plugin, self)
            )
        self._flush_unload()

    def handle_command(self, plugin_name: str, msg: dict):
        """路由自定义命令到指定插件；未挂载或未处理返回 None"""
        with self._lock:
            inst = self.get(plugin_name)
        if inst is None:
            return None
        try:
            return inst.handle_command(PluginContext(self._session, inst, self), msg)
        except Exception:
            _logger.exception(
                "插件 %s handle_command 异常 (会话 %s)", plugin_name, self._session.id
            )
            return None

    def inspect_state(self) -> Optional[dict]:
        """命令返回时的一次性状态检查

        按挂载顺序调用各插件的 inspect_state，首个返回非 None 结果的生效；
        插件异常被隔离，不影响响应构造。
        """
        for plugin in list(self._plugins):
            try:
                result = plugin.inspect_state(
                    PluginContext(self._session, plugin, self)
                )
            except Exception:
                _logger.exception(
                    "插件 %s inspect_state 异常 (会话 %s)",
                    plugin.name,
                    self._session.id,
                )
                continue
            if result is not None:
                return result
        return None

    # ── 工具 ──────────────────────────────────────────────

    def _safe_call(self, plugin: Plugin, method_name: str, *args) -> None:
        try:
            getattr(plugin, method_name)(*args)
        except Exception:
            _logger.exception(
                "插件 %s %s 异常 (会话 %s)", plugin.name, method_name, self._session.id
            )
