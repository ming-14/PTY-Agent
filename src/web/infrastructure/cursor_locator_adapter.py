"""鼠标增强光标定位器适配器。

封装 bin/cursorlocator 模块，提供 start/stop/status 接口。
cursorlocator 是单例模块（_instance 全局唯一），本适配器保证不会重复启动。
"""

import logging
import sys
import os

from ..application.ports import CursorLocatorServicePort

_logger = logging.getLogger("pty-web")

_BIN_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "bin",
))


class CursorLocatorAdapter(CursorLocatorServicePort):
    """光标定位器适配器：管理 cursorlocator 模块的启停。"""

    def __init__(self):
        self._module = None
        self._running = False
        self._available = False
        self._import_error = None
        self._config = None
        try:
            if sys.platform != "win32":
                self._import_error = "cursorlocator 仅支持 Windows"
                _logger.info("CursorLocator: not available (non-Windows platform)")
                return
            if _BIN_DIR not in sys.path:
                sys.path.insert(0, _BIN_DIR)
            import cursorlocator as _mod
            self._module = _mod
            self._available = True
            from cursorlocator.config import Config as _Cfg
            self._config = _Cfg()
            _logger.info("CursorLocator: module loaded from %s", _BIN_DIR)
        except Exception as e:
            self._import_error = str(e)
            _logger.warning("CursorLocator: module import failed: %s", e)

    def is_available(self) -> bool:
        return self._available

    def start(self) -> dict:
        if not self._available:
            return {"running": False, "error": self._import_error or "cursorlocator 不可用"}
        if self._running:
            return {"running": True}
        try:
            self._module.start()
            self._running = True
            _logger.info("CursorLocator: started")
            return {"running": True}
        except Exception as e:
            _logger.exception("CursorLocator: start failed")
            return {"running": False, "error": str(e)}

    def stop(self) -> dict:
        if not self._available:
            return {"running": False, "error": self._import_error or "cursorlocator 不可用"}
        if not self._running:
            return {"running": False}
        try:
            self._module.stop()
            self._running = False
            _logger.info("CursorLocator: stopped")
            return {"running": False}
        except Exception as e:
            _logger.exception("CursorLocator: stop failed")
            return {"running": True, "error": str(e)}

    def get_status(self) -> dict:
        result = {
            "running": self._running,
            "available": self._available,
        }
        if self._config:
            result["outer_radius"] = self._config.get("outer_radius")
            result["inner_radius"] = self._config.get("inner_radius")
            result["alpha"] = self._config.get("alpha")
        return result

    def update_config(self, **kwargs) -> dict:
        if not self._available:
            return {"error": self._import_error or "cursorlocator 不可用"}
        try:
            if self._running:
                self._module.update_config(**kwargs)
            if self._config:
                self._config.set(**kwargs)
                self._config.save()
            _logger.info("CursorLocator: config updated: %s", kwargs)
            return {}
        except Exception as e:
            _logger.exception("CursorLocator: update_config failed")
            return {"error": str(e)}
