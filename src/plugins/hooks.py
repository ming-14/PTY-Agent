"""钩子链引擎 — 优先级排序与五类调度语义

插件在挂载/启用时向引擎注册已实现的钩子；按优先级（默认 100，高者先）与
注册顺序编译为链，调用时按钩子类型选择语义：

- modify    链式变换：前一输出为后一输入，任一返回 None 即拦截（输入类）
- observe   只通知：返回值忽略
- intercept 可取消：True=放行即停，False=拒绝即停，None=不表态继续
- provide   提供者：按优先级升序，首个非 None 生效
- aggregate 收集：所有返回值合并为列表

异常隔离：单个钩子抛异常只记日志，不影响链上其余钩子与主流程。
链为空时所有调用零开销短路。
"""

import threading
from typing import Callable, Dict, List, Optional

from .base import Plugin, VALID_HOOKS
from ..logging import get_logger

_logger = get_logger("pty-plugins")

DEFAULT_PRIORITY = 100


class _Hook:
    """链上单条钩子：优先级 + 绑定方法 + 所属插件 + 注册顺序"""

    __slots__ = ("priority", "method", "plugin", "order")

    def __init__(self, priority: int, method, plugin, order: int):
        self.priority = priority
        self.method = method
        self.plugin = plugin
        self.order = order


class HookEngine:
    """钩子链引擎（线程安全：注册/卸载加锁，调用读排序快照）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._chains: Dict[str, List[_Hook]] = {}
        self._order = 0

    # ── 注册/卸载 ─────────────────────────────────────────

    def register(self, plugin, manifest=None) -> None:
        """注册插件已实现的钩子

        manifest 缺省时读 plugin.manifest；on_event/on_poll 按清单 triggers
        门控（未声明不注册）；handle_message 不经会话钩子链（dispatcher 直调）。
        """
        if manifest is None:
            manifest = getattr(plugin, "manifest", None)
        decl = manifest.hooks if manifest is not None else {}
        triggers = manifest.triggers if manifest is not None else ["event", "poll"]
        with self._lock:
            self._order += 1
            for name in VALID_HOOKS:
                if name == "handle_message":
                    continue
                if name == "on_event" and "event" not in triggers:
                    continue
                if name == "on_poll" and "poll" not in triggers:
                    continue
                method = getattr(plugin, name, None)
                if method is None or getattr(method, "__func__", None) is getattr(
                    Plugin, name, None
                ):
                    continue
                priority = DEFAULT_PRIORITY
                hook_decl = decl.get(name)
                if isinstance(hook_decl, dict) and isinstance(
                    hook_decl.get("priority"), int
                ):
                    priority = hook_decl["priority"]
                self._chains.setdefault(name, []).append(
                    _Hook(priority, method, plugin, self._order)
                )

    def unregister(self, plugin) -> None:
        """移除插件全部钩子（卸载/停用时调用）"""
        with self._lock:
            for chain in self._chains.values():
                chain[:] = [h for h in chain if h.plugin is not plugin]

    # ── 调度 ──────────────────────────────────────────────

    def _chain(self, name: str) -> List[_Hook]:
        """取钩子链快照（modify/observe/intercept 语义：优先级降序 + 注册序）"""
        with self._lock:
            chain = self._chains.get(name)
            if not chain:
                return []
            return sorted(chain, key=lambda h: (-h.priority, h.order))

    def dispatch_modify(self, name: str, ctx_factory, value, predicate=None, intercept=True):
        """链式变换：intercept=True 时任一插件返回 None 即拦截（整体返回 None）；
        intercept=False 时返回 None 视为"不修改"，沿用上一值继续（CLI 请求/响应钩子）"""
        for hook in self._chain(name):
            if predicate is not None and not predicate(hook.plugin):
                continue
            try:
                result = hook.method(ctx_factory(hook.plugin), value)
            except Exception:
                _logger.exception("插件 %s %s 异常", hook.plugin.name, name)
                continue
            if result is None:
                if intercept:
                    _logger.info("插件 %s 拦截 %s", hook.plugin.name, name)
                    return None
                continue
            value = result
        return value

    def dispatch_observe(self, name: str, ctx_factory, *args, predicate=None):
        """只通知：返回值忽略"""
        for hook in self._chain(name):
            if predicate is not None and not predicate(hook.plugin):
                continue
            try:
                hook.method(ctx_factory(hook.plugin), *args)
            except Exception:
                _logger.exception("插件 %s %s 异常", hook.plugin.name, name)

    def dispatch_provide(self, name: str, ctx_factory, *args, predicate=None):
        """提供者：低优先级在前，首个非 None 生效"""
        chain = self._chain(name)
        chain.sort(key=lambda h: (h.priority, h.order))
        for hook in chain:
            if predicate is not None and not predicate(hook.plugin):
                continue
            try:
                result = hook.method(ctx_factory(hook.plugin), *args)
            except Exception:
                _logger.exception("插件 %s %s 异常", hook.plugin.name, name)
                continue
            if result is not None:
                return result
        return None

    def dispatch_intercept(self, name: str, ctx_factory, *args, predicate=None):
        """可取消：True=放行即停，False=拒绝即停，None=不表态继续"""
        for hook in self._chain(name):
            if predicate is not None and not predicate(hook.plugin):
                continue
            try:
                result = hook.method(ctx_factory(hook.plugin), *args)
            except Exception:
                _logger.exception("插件 %s %s 异常", hook.plugin.name, name)
                continue
            if result is True:
                return True
            if result is False:
                return False
        return None

    def dispatch_aggregate(self, name: str, ctx_factory, *args, predicate=None):
        """收集：所有返回值合并为列表"""
        results = []
        for hook in self._chain(name):
            if predicate is not None and not predicate(hook.plugin):
                continue
            try:
                results.append(hook.method(ctx_factory(hook.plugin), *args))
            except Exception:
                _logger.exception("插件 %s %s 异常", hook.plugin.name, name)
        return results