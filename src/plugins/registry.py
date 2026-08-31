"""插件注册表 — 清单驱动加载 + 生命周期编排

守护进程启动时按目录发现加载一次；持有每个插件的清单/类/状态/错误，
提供：
- 生命周期：enable/disable/reload/load_dir（安装）/remove（卸载）
- 进程级插件单例实例（消息路由表来源）
- 会话级插件按名实例化（挂载用）
- auto_load 条件匹配（exec 按 command/cwd/env 判定）
- 变更回调（进程级实例集合变化 → dispatcher 同步消息路由）

状态机：LOADED（已加载未启用）→ ENABLED（on_init+on_enable 完成）→
DISABLED/LOADED；加载或初始化失败 → BROKEN（error 可见，不参与运行）。
"""

import os
import re
import sys
from typing import Callable, Dict, List, Optional

from .base import Plugin, PluginContext, ProcessPluginContext
from .environment import PluginEnvironment
from .loader import LoadedPlugin, load_plugin_dir, load_plugins, module_name
from .cli_options import check_cli_option_conflicts
from ..logging import get_logger

_logger = get_logger("pty-plugins")

STATE_LOADED = "loaded"
STATE_ENABLED = "enabled"
STATE_DISABLED = "disabled"
STATE_BROKEN = "broken"


class _Entry:
    """单个插件的运行时登记"""

    __slots__ = ("manifest", "cls", "instance", "state", "error", "auto_load", "subs")

    def __init__(self, loaded: LoadedPlugin):
        self.manifest = loaded.manifest
        self.cls = loaded.cls
        self.instance = None
        self.state = STATE_LOADED
        self.error = None
        self.auto_load = loaded.manifest.auto_load
        self.subs = []  # 事件总线订阅 (pattern, callback) 引用，用于 unsubscribe


def _match_cwd(pattern: str, cwd: str) -> bool:
    """cwd 匹配：'^' 开头视为正则，否则按前缀"""
    if pattern.startswith("^"):
        return re.search(pattern, cwd) is not None
    return cwd.startswith(pattern)


def _match_auto_load(
    rule: dict, command, cwd: Optional[str], env: Optional[dict]
) -> bool:
    """按插件声明的 auto_load 规则判定是否命中（所有声明维度均需命中）"""
    rule_cmd = rule.get("command")
    if rule_cmd:
        cmd_str = command if isinstance(command, str) else " ".join(command or [])
        if isinstance(rule_cmd, str):
            if re.search(rule_cmd, cmd_str) is None:
                return False
        else:
            if not any(keyword in cmd_str for keyword in rule_cmd):
                return False
    rule_cwd = rule.get("cwd")
    if rule_cwd:
        if not cwd or not any(_match_cwd(p, cwd) for p in rule_cwd):
            return False
    rule_env = rule.get("env")
    if rule_env:
        env_map = env or {}
        for var, pattern in rule_env.items():
            actual = env_map.get(var)
            if pattern in (None, ""):
                if actual is None:
                    return False
            elif actual is None or re.search(str(pattern), actual) is None:
                return False
    return True


class PluginRegistry:
    """插件注册表（daemon 进程级）"""

    def __init__(
        self,
        plugin_dirs: List[str],
        states: Optional[dict] = None,
        policy: Optional[dict] = None,
        state_store=None,
        enabled_by_default: bool = True,
    ):
        self.environment = PluginEnvironment(policy)
        self._entries: Dict[str, _Entry] = {}
        self._all_manifests: Dict[str, object] = {}  # 全部已加载清单（含 cli 形态，交叉冲突检测用）
        self._change_cb: Optional[Callable] = None
        self._state_store = state_store
        self._states = dict(states or {})
        self._enabled_by_default = enabled_by_default
        for item in load_plugins(plugin_dirs):
            self._register_loaded(item)
        self._refresh_conflicts()
        for name in list(self._entries):
            if self._states.get(name, enabled_by_default):
                try:
                    self.enable(name)
                except Exception:
                    _logger.exception("插件启用失败: %s", name)

    # ── 内部注册 ──────────────────────────────────────────

    def _register_loaded(self, loaded: LoadedPlugin) -> None:
        manifest = loaded.manifest
        if manifest.id in self._entries:
            _logger.error(
                "插件名冲突，跳过 %s（已存在: %s）",
                manifest.id,
                self._entries[manifest.id].manifest.path,
            )
            return
        # 全部清单登记
        self._all_manifests[manifest.id] = manifest
        entry = _Entry(loaded)
        self._entries[manifest.id] = entry
        try:
            self.environment.register(manifest)
        except Exception as e:
            entry.state = STATE_BROKEN
            entry.error = "环境初始化失败: %s" % e
            _logger.exception("插件 %s 环境初始化失败", manifest.id)
            return
        # 初始状态：registry.json 记录禁用 → DISABLED（不参与运行，可随时 enable）
        if not self._states.get(manifest.id, self._enabled_by_default):
            entry.state = STATE_DISABLED

    def _refresh_conflicts(self) -> None:
        """按当前全部已加载清单重算 CLI 选项冲突

        新冲突插件置 BROKEN 不加载（错误可见，plugin list/info）；
        冲突已解除（对方卸载/重载修复）的插件恢复登记状态（按 registry.json
        状态为 LOADED/DISABLED），可在 enable 循环/显式 enable 中恢复。
        """
        conflicted = check_cli_option_conflicts(list(self._all_manifests.values()))
        for name, err in conflicted.items():
            entry = self._entries.get(name)
            if entry is None or entry.state == STATE_BROKEN:
                continue
            if entry.state == STATE_ENABLED:
                # 已启用插件遇新冲突：先按 disable 同款逻辑停用运行实例，
                # 避免 BROKEN 后进程级路由/会话钩子仍在执行
                self._unsubscribe_events(name, entry)
                if entry.instance is not None:
                    try:
                        pctx = ProcessPluginContext(
                            None, entry.instance, None, self.environment
                        )
                        entry.instance.on_disable(pctx)
                    except Exception:
                        _logger.exception("插件 %s on_disable 异常", name)
                    entry.instance = None
                self._notify_change()
            entry.state = STATE_BROKEN
            entry.error = "CLI 选项冲突: %s" % err
            _logger.error("插件 %s 因 CLI 选项冲突不加载: %s", name, err)
        for name, entry in self._entries.items():
            if entry.state != STATE_BROKEN or name in conflicted:
                continue
            if not (entry.error or "").startswith("CLI 选项冲突"):
                continue
            entry.error = ""
            entry.state = (
                STATE_DISABLED
                if not self._states.get(name, self._enabled_by_default)
                else STATE_LOADED
            )
            _logger.info("插件 %s CLI 选项冲突已解除，恢复登记", name)

    # ── 查询 ──────────────────────────────────────────────

    def has(self, name: str) -> bool:
        return name in self._entries

    def describe(self, name: str) -> Optional[dict]:
        """查询插件元信息（kind/messageTypes）；未登记返回 None"""
        entry = self._entries.get(name)
        if entry is None:
            return None
        return {
            "kind": "/".join(entry.manifest.kind),
            "messageTypes": list(entry.manifest.message_types),
        }

    def instantiate(self, name: str):
        """按名实例化会话级插件（挂载用）；未加载/BROKEN/进程级/未启用返回 None"""
        entry = self._entries.get(name)
        if entry is None or entry.state != STATE_ENABLED or "session" not in entry.manifest.kind:
            return None
        return entry.cls()

    def list_all(self) -> List[dict]:
        """全部已加载插件的信息快照（plugin list 命令用）"""
        return [
            {
                "name": m.id,
                "version": m.version,
                "description": m.description,
                "kind": "/".join(m.kind),
                "state": e.state,
                "error": e.error or "",
                "triggers": list(m.triggers),
                "pollInterval": m.poll_interval,
                "autoLoad": bool(m.auto_load),
                "messageTypes": list(m.message_types),
                "needsIO": m.needs_io,
                "commands": list(m.commands),
                "hooks": dict(m.hooks),
                "permissions": list(m.permissions),
                "cliOptions": [o.to_dict() for o in m.cli_options],
            }
            for name, e in sorted(self._entries.items())
            for m in (e.manifest,)
        ]

    def info(self, name: str) -> Optional[dict]:
        """单个插件详情（plugin info/status 命令用）"""
        entry = self._entries.get(name)
        if entry is None:
            return None
        m = entry.manifest
        return {
            "name": m.id,
            "version": m.version,
            "description": m.description,
            "kind": "/".join(m.kind),
            "state": entry.state,
            "error": entry.error or "",
            "path": m.path,
            "triggers": list(m.triggers),
            "pollInterval": m.poll_interval,
            "autoLoad": m.auto_load,
            "messageTypes": list(m.message_types),
            "needsIO": m.needs_io,
            "commands": list(m.commands),
            "hooks": dict(m.hooks),
            "permissions": list(m.permissions),
            "events": list(m.events),
            "dependencies": dict(m.dependencies),
            "cliOptions": [o.to_dict() for o in m.cli_options],
        }

    def process_instances(self) -> Dict[str, Plugin]:
        """已启用且实例化的插件实例字典（所有形态，dispatcher 路由用）

        按 message_types 注册路由：无 messageTypes 的插件（纯 cli）在循环中
        因类型不匹配跳过，不会参与消息路由或响应装饰。
        """
        return {
            name: e.instance
            for name, e in self._entries.items()
            if e.state == STATE_ENABLED and e.instance is not None
        }

    # ── 生命周期 ──────────────────────────────────────────

    def set_change_callback(self, cb) -> None:
        """设置进程级插件变更回调（dispatcher 同步消息路由）"""
        self._change_cb = cb

    def _notify_change(self) -> None:
        if self._change_cb is not None:
            try:
                self._change_cb()
            except Exception:
                _logger.exception("插件变更回调异常")

    def _set_state(self, name: str, state: str, error: str = "") -> None:
        entry = self._entries[name]
        entry.state = state
        if error:
            entry.error = error
        self._states[name] = state == STATE_ENABLED
        if self._state_store is not None:
            self._state_store.set(name, state == STATE_ENABLED)

    def enable(self, name: str) -> bool:
        """启用插件

        订阅事件总线模式；process/session 形态构造规范实例并回调 on_init/on_enable；
        失败置 BROKEN 并返回 False。
        """
        entry = self._entries.get(name)
        if entry is None:
            _logger.warning("插件不存在: %s", name)
            return False
        if entry.state == STATE_BROKEN:
            _logger.warning("插件已损坏，不可启用: %s", name)
            return False
        if entry.state == STATE_ENABLED:
            return True

        # 构造规范实例
        try:
            inst = entry.cls()
        except Exception:
            _logger.exception("插件 %s 实例化失败，置 BROKEN", name)
            entry.state = STATE_BROKEN
            entry.error = "实例化失败"
            return False

        pctx = ProcessPluginContext(None, inst, None, self.environment)
        try:
            inst.on_init(pctx)
            inst.on_enable(pctx)
        except Exception:
            _logger.exception("插件 %s on_init/on_enable 异常，置 BROKEN", name)
            entry.state = STATE_BROKEN
            entry.error = "初始化异常"
            return False

        entry.instance = inst
        self._set_state(name, STATE_ENABLED)
        self._subscribe_events(name, entry)
        self._notify_change()
        _logger.info("插件已启用: %s", name)
        return True

    def disable(self, name: str) -> bool:
        """停用插件：取消订阅、回调 on_disable、释放实例"""
        entry = self._entries.get(name)
        if entry is None or entry.state != STATE_ENABLED:
            return False
        self._unsubscribe_events(name, entry)
        if entry.instance is not None:
            try:
                pctx = ProcessPluginContext(None, entry.instance, None, self.environment)
                entry.instance.on_disable(pctx)
            except Exception:
                _logger.exception("插件 %s on_disable 异常", name)
            entry.instance = None
        self._set_state(name, STATE_DISABLED)
        self._notify_change()
        _logger.info("插件已禁用: %s", name)
        return True

    def reload(self, name: str) -> bool:
        """热重载：disable → 重新导入模块 → 重新登记 → 按原状态 enable

        原为禁用状态的插件重载后保持禁用（state=DISABLED）。
        """
        entry = self._entries.get(name)
        if entry is None:
            return False
        was_enabled = entry.state == STATE_ENABLED
        if was_enabled and not self.disable(name):
            return False
        path = entry.manifest.path
        self._entries.pop(name, None)
        self._all_manifests.pop(name, None)
        self.environment.unregister(name)
        sys.modules.pop(module_name(name), None)
        loaded = load_plugin_dir(path)
        if loaded is None:
            _logger.error("插件重载失败: %s", name)
            return False
        self._register_loaded(loaded)
        # 冲突刷新在状态设置前：重载后仍冲突的插件保持 BROKEN 不被 enable 覆盖
        self._refresh_conflicts()
        if was_enabled:
            return self.enable(name)
        # 原禁用状态：置为 DISABLED（冲突插件保持 BROKEN）
        entry = self._entries[name]
        if entry.state != STATE_BROKEN:
            entry.state = STATE_DISABLED
        return True

    def load_dir(self, plugin_dir: str, dest_root: str) -> Optional[str]:
        """安装（从目录）：校验清单 → 复制到插件目录 → 登记（LOADED，不自动启用）

        Args:
            plugin_dir: 源插件目录（含 plugin.json）
            dest_root: 插件安装根目录（config/plugins）
        Returns:
            插件 id；失败返回 None（错误已记日志）
        """
        import shutil
        loaded = load_plugin_dir(plugin_dir)
        if loaded is None:
            return None
        dest = os.path.join(dest_root, loaded.manifest.id)
        if os.path.exists(dest):
            _logger.error("插件已存在，拒绝安装覆盖: %s", dest)
            return None
        shutil.copytree(plugin_dir, dest)
        re_loaded = load_plugin_dir(dest)
        if re_loaded is None:
            shutil.rmtree(dest, ignore_errors=True)
            return None
        self._register_loaded(re_loaded)
        self._refresh_conflicts()
        return re_loaded.manifest.id

    def remove(self, name: str) -> bool:
        """卸载：须先 disable；清除数据目录、插件目录、登记与状态"""
        entry = self._entries.get(name)
        if entry is None:
            return False
        if entry.state == STATE_ENABLED:
            _logger.error("插件 %s 已启用，需先 disable", name)
            return False
        storage_root = (
            self.environment.storage_for(name).root
            if self.environment.storage_for(name) is not None
            else None
        )
        self.environment.unregister(name)
        import shutil
        if storage_root:
            shutil.rmtree(storage_root, ignore_errors=True)
        shutil.rmtree(entry.manifest.path, ignore_errors=True)
        self._entries.pop(name, None)
        self._all_manifests.pop(name, None)
        self._refresh_conflicts()
        if self._state_store is not None:
            self._state_store.delete(name)
        _logger.info("插件已卸载: %s", name)
        return True

    # ── 事件总线订阅 ──────────────────────────────────────

    def _subscribe_events(self, name: str, entry: _Entry) -> None:
        for pattern in entry.manifest.events:
            cb = self._make_bus_callback(name)
            self.environment.events.subscribe(pattern, cb)
            entry.subs.append((pattern, cb))

    def _unsubscribe_events(self, name: str, entry: _Entry) -> None:
        for pattern, cb in entry.subs:
            self.environment.events.unsubscribe(pattern, cb)
        entry.subs.clear()

    def _make_bus_callback(self, name: str):
        def _on_event(event):
            entry = self._entries.get(name)
            if entry is None or entry.instance is None:
                return
            try:
                ctx = PluginContext(None, entry.instance, environment=self.environment)
                entry.instance.on_bus_event(ctx, event)
            except Exception:
                _logger.exception("插件 %s on_bus_event 异常 topic=%s", name, event.topic)
        return _on_event

    # ── 自动加载 ──────────────────────────────────────────

    def match_auto_load(self, command, cwd: Optional[str], env: Optional[dict]) -> List[str]:
        """按 exec 请求字段（command/cwd/env）返回 auto_load 命中的插件名列表"""
        hits = []
        for name, entry in self._entries.items():
            if entry.state != STATE_ENABLED:
                continue
            if "session" not in entry.manifest.kind:
                continue
            rule = entry.auto_load
            if rule is None:
                continue
            try:
                if _match_auto_load(rule, command, cwd, env):
                    hits.append(name)
            except Exception:
                _logger.warning("插件 %s auto_load 规则判定异常，视为不命中", name)
        if hits:
            _logger.info("自动加载命中: %s", hits)
        return hits