"""可选模块惰性导入网关。

集中管理所有"可裁剪/可选"模块的可用性探测与惰性导入，避免各模块散落
try/except ImportError 保护。任何可选模块缺失时，本网关返回 None / False，
相应功能优雅降级（unavailable/disabled），不影响主流程启动。

覆盖范围（与 config 开关联动）：
  - web          : src/web（ENABLE_WEB 且模块可导入）
  - vnc          : src/vnc（ENABLE_WEB 且 ENABLE_VNC 且模块可导入）
  - screenshare  : src/screenshare（ENABLE_WEB 且 ENABLE_FASTSCREEN 且模块可导入）
  - cursorlocator: src/web/infrastructure/cursor_locator_adapter（ENABLE_WEB 且模块可导入）
  - sandbox      : src/sandbox（sandbox 启用且模块可导入）
  - plugins      : src/plugins（插件配置启用）

设计原则：
- 本模块是唯一"知道某个可选模块存不存在"的地方；其余代码只从本模块取结果。
- 探测结果缓存；缺失时返回 None，绝不向调用方抛 ImportError。
- 配置开关惰性读取，配置加载异常时按默认值（False）处理，保证网关自身可导入。
"""

import importlib
from typing import Optional

# 探测结果缓存：key=(module, attr) -> obj | None
_CACHE: dict = {}


def _probe(module: str, attr: Optional[str] = None):
    """惰性导入并缓存；缺失或导入异常返回 None，绝不在此抛错。"""
    key = (module, attr)
    if key not in _CACHE:
        try:
            mod = importlib.import_module(module)
            _CACHE[key] = getattr(mod, attr) if attr else mod
        except Exception:
            _CACHE[key] = None
    return _CACHE[key]


def _flag(name: str, default: bool = False) -> bool:
    """惰性读取 daemon 配置开关；配置加载异常时按默认值处理。"""
    try:
        from src.config import daemon as _cfg
        return bool(getattr(_cfg, name, default))
    except Exception:
        return default


# ────────────────────────────── web ──────────────────────────────

def web_available() -> bool:
    """web 是否可用（ENABLE_WEB 且 src/web 可导入）。"""
    return _flag("ENABLE_WEB") and _probe("src.web") is not None


def get_web_server_cls():
    """返回 WebServer 类；web 不可用返回 None。"""
    if not web_available():
        return None
    return _probe("src.web.presentation.server", "WebServer")


def get_history_store_cls():
    """返回 HistoryStore 类；历史归档模块不可导入时返回 None（归档禁用）

    历史归档属于 daemon 核心能力（会话结束即时归档 + events 回退查询），
    独立于 web 可用性：仅 src/web 整体被裁剪时返回 None。
    """
    return _probe("src.web.infrastructure.repositories.history_store", "HistoryStore")


# ────────────────────────────── vnc ──────────────────────────────

def vnc_available() -> bool:
    """vnc 是否可用（web + ENABLE_VNC + src/vnc 可导入）。"""
    if not web_available():
        return False
    if not _flag("ENABLE_VNC"):
        return False
    return _probe("src.vnc") is not None


def get_vnc_adapter_cls():
    """返回 VncAdapter 类；vnc 不可用返回 None。"""
    if not vnc_available():
        return None
    return _probe("src.vnc", "VncAdapter")


# ─────────────────────────── screenshare ──────────────────────────

def screenshare_available() -> bool:
    """screenshare 是否可用（web + ENABLE_FASTSCREEN + src/screenshare 可导入）。"""
    if not web_available():
        return False
    if not _flag("ENABLE_FASTSCREEN"):
        return False
    return _probe("src.screenshare") is not None


def get_screenshare_adapter_cls():
    """返回 ScreenshareAdapter 类；screenshare 不可用返回 None。"""
    if not screenshare_available():
        return None
    return _probe("src.screenshare", "ScreenshareAdapter")


# ─────────────────────────── cursorlocator ─────────────────────────

def cursor_locator_available() -> bool:
    """cursorlocator 是否可用（web + 适配器模块可导入）。"""
    if not web_available():
        return False
    return _probe("src.web.infrastructure.cursor_locator_adapter") is not None


def get_cursor_locator_adapter_cls():
    """返回 CursorLocatorAdapter 类；不可用返回 None。"""
    if not cursor_locator_available():
        return None
    return _probe("src.web.infrastructure.cursor_locator_adapter", "CursorLocatorAdapter")


# ───────────────────────────── sandbox ────────────────────────────

def sandbox_available() -> bool:
    """sandbox 是否可用（sandbox 配置启用 且 src/sandbox 可导入）。"""
    try:
        from src.config import sandbox as _sbx
        if not _sbx.ENABLED:
            return False
    except Exception:
        return False
    return _probe("src.sandbox") is not None


# ───────────────────────────── plugins ────────────────────────────

def plugins_available() -> bool:
    """插件系统是否可用（registry.json 存在且启用）。"""
    try:
        from src.config import plugins as _plg
        return bool(_plg.ENABLED)
    except Exception:
        return False


__all__ = [
    "cursor_locator_available",
    "get_cursor_locator_adapter_cls",
    "get_history_store_cls",
    "get_screenshare_adapter_cls",
    "get_vnc_adapter_cls",
    "get_web_server_cls",
    "plugins_available",
    "sandbox_available",
    "screenshare_available",
    "vnc_available",
    "web_available",
]