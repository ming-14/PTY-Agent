"""插件注册表 — 进程级插件集合

守护进程启动时扫描加载一次，向会话管理与命令层提供：
- 按名查询/实例化插件类
- 全部插件信息快照（plugin list）
- 自动加载条件匹配（auto_load：exec 请求按 command/cwd/env 判定命中）
"""

import logging
import re
from typing import Dict, List, Optional

from .base import Plugin
from .loader import load_plugins

_logger = logging.getLogger("pty-plugins")


def _match_cwd(pattern: str, cwd: str) -> bool:
    """cwd 匹配：'^' 开头视为正则，否则按前缀"""
    if pattern.startswith("^"):
        return re.search(pattern, cwd) is not None
    return cwd.startswith(pattern)


def _match_auto_load(
    rule: dict, command, cwd: Optional[str], env: Optional[dict]
) -> bool:
    """按插件声明的 auto_load 规则判定是否命中（所有声明维度均需命中）

    rule 结构:
        command: str 正则，或 list 关键词（任一命中）
        cwd:     list，元素为前缀或 '^' 开头的正则
        env:     dict，变量名 → 正则（空值表示仅要求变量存在）
    """
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
    """进程级插件注册表

    加载失败/重名的插件被跳过并记录日志，不影响其余插件与主流程。
    进程级插件（message_types 非空）在加载时单例实例化并常驻，
    跨会话/连接共享实例状态（如文件状态机、传输映射表）。
    """

    def __init__(self, plugin_paths: List[str]):
        self._classes: Dict[str, type] = {}
        self._source: Dict[str, str] = {}
        self._process_instances: Dict[str, Plugin] = {}
        for cls in load_plugins(plugin_paths):
            name = cls.name
            if name in self._classes:
                _logger.error(
                    "插件名冲突，跳过 %s（已存在: %s）", name, self._source.get(name)
                )
                continue
            # 进程级插件：实例化失败则完全不注册。
            # 若仍注册到 _classes，has()/list_all() 会报告"已加载"但
            # process_instances() 无实例、instantiate() 返回 None——可见但不可用，
            # 状态不一致；进程级插件的核心价值即消息处理，实例化失败等于不可用。
            if cls.message_types:
                try:
                    instance = cls()
                except Exception:
                    _logger.exception("进程级插件实例化失败，跳过: %s", name)
                    continue
                self._process_instances[name] = instance
            self._classes[name] = cls
            self._source[name] = cls.__module__
            _logger.info(
                "插件已加载: %s v%s 触发=%s 间隔=%s 消息=%s io=%s",
                name,
                cls.version,
                list(cls.triggers),
                cls.poll_interval if cls.poll_interval else "-",
                list(cls.message_types),
                cls.needs_io,
            )

    def has(self, name: str) -> bool:
        return name in self._classes

    def get(self, name: str) -> Optional[type]:
        return self._classes.get(name)

    def instantiate(self, name: str) -> Optional[Plugin]:
        """按名实例化 session 级插件；未加载或进程级插件时返回 None

        进程级插件已单例常驻，不可挂载到会话（消息语义不匹配）。
        """
        cls = self._classes.get(name)
        if cls is None or cls.message_types:
            return None
        return cls()

    def process_instances(self) -> Dict[str, Plugin]:
        """进程级插件实例（message_type → Plugin），dispatcher 路由用

        返回副本以隔离内部字典：调用方修改不污染注册表状态。
        dispatcher 仅在启动时 _build_registry 调用一次，副本开销可忽略。
        """
        return dict(self._process_instances)

    def list_all(self) -> List[dict]:
        """全部已加载插件的信息快照（plugin list 命令用）"""
        return [
            {
                "name": cls.name,
                "version": cls.version,
                "description": cls.description,
                "triggers": list(cls.triggers),
                "pollInterval": cls.poll_interval,
                "autoLoad": bool(cls.auto_load),
                "messageTypes": list(cls.message_types),
                "needsIO": cls.needs_io,
            }
            for cls in self._classes.values()
        ]

    def match_auto_load(
        self, command, cwd: Optional[str], env: Optional[dict]
    ) -> List[str]:
        """按 exec 请求字段（command/cwd/env）返回 auto_load 命中的插件名列表"""
        hits = []
        for name, cls in self._classes.items():
            rule = cls.auto_load
            if not rule:
                continue
            try:
                if _match_auto_load(rule, command, cwd, env):
                    hits.append(name)
            except Exception:
                _logger.warning("插件 %s auto_load 规则判定异常，视为不命中", name)
        if hits:
            _logger.info("自动加载命中: %s", hits)
        return hits
