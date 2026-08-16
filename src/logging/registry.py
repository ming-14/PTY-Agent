"""logger 名注册表 — 单一事实来源，防止配置遗漏导致日志丢失

启动装配时从配置加载所有分组 → 注册表。
get_logger() 时若配置已加载且 name 未注册，告警一次（防刷屏）。
"""

import logging
import threading
from typing import Dict, List

_lock = threading.Lock()
_registered: Dict[str, str] = {}
_warned: set = set()
_config_loaded: bool = False

# 告警用 logger，直接用标准 logging，不走 get_logger 避免递归
_warn_logger = logging.getLogger("pty-logging-registry")


def register_group(group: str, names: List[str]) -> None:
    """注册一个分组的所有 logger 名（装配时从配置加载调用）"""
    with _lock:
        for name in names:
            _registered[name] = group


def mark_config_loaded() -> None:
    """标记配置已加载，此后 get_logger 对未注册名告警"""
    global _config_loaded
    with _lock:
        _config_loaded = True


def get_logger(name: str) -> logging.Logger:
    """获取 logger

    若配置已加载且 name 未注册，告警一次（说明代码用了配置没定义的 logger，
    日志可能丢失）。未加载配置时不告警（模块级 import 阶段）。
    """
    with _lock:
        should_warn = _config_loaded and name not in _registered and name not in _warned
        if should_warn:
            _warned.add(name)
    if should_warn:
        _warn_logger.warning("logger %r 未在配置中注册，日志可能丢失", name)
    return logging.getLogger(name)


def is_registered(name: str) -> bool:
    with _lock:
        return name in _registered


def get_registered() -> Dict[str, str]:
    """返回已注册的 logger name -> group 映射"""
    with _lock:
        return dict(_registered)


def reset() -> None:
    """重置注册表状态（测试用）"""
    global _config_loaded
    with _lock:
        _registered.clear()
        _warned.clear()
        _config_loaded = False
