"""FastScreen 服务：实现 FastScreenServicePort，桥接 CaptureEngine + StreamManager。

负责：
- 根据 ENABLE_FASTSCREEN 配置决定是否启用
- 配置 sys.path 以便导入 fastscreencore 包（bin/）
- 懒加载 CaptureEngine + StreamManager 单例
- 对外暴露 FastScreenServicePort 接口
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from .ports import FastScreenServicePort
from ..config.common import PROJECT_ROOT
from ..config.daemon import ENABLE_FASTSCREEN

_logger = logging.getLogger("pty-fastscreenservice")


def _resolve_bin_dir() -> Path:
    """推导 bin/ 目录（包含 fastscreencore 包）。

    路径：src/fastscreen/adapter.py → src/fastscreen/ → src/ → 项目根 → bin/
    """
    here = Path(__file__).resolve()
    project_root = here.parents[2]  # 项目根
    return project_root / "bin"


def _ensure_path(path: Path) -> None:
    """将目录加入 sys.path（若尚未存在）。"""
    s = str(path)
    if s not in sys.path:
        sys.path.insert(0, s)


_fastscreen_loaded = False
_stream_manager = None
_capture_engine = None


def _load_fastsreen_modules():
    """懒加载 fastscreencore + streamers 模块。

    将 bin/ 加入 sys.path 后导入：
    - fastscreencore.CaptureEngine（底层捕获引擎）
    - .streamers.manager.StreamManager（多客户端共享会话管理器）
    """
    global _fastscreen_loaded, _stream_manager, _capture_engine
    if _fastscreen_loaded:
        return

    try:
        _ensure_path(_resolve_bin_dir())

        from fastscreencore import CaptureEngine  # noqa: F401
        from fastscreencore import CaptureMethod, TargetType  # noqa: F401

        from .streamers.manager import StreamManager

        _capture_engine = CaptureEngine()
        _stream_manager = StreamManager.get()
        _fastscreen_loaded = True
        _logger.info(
            "FastScreen modules loaded: capture_engine=%s stream_manager=%s",
            _capture_engine is not None, _stream_manager is not None,
        )
    except Exception as e:
        _logger.exception("FastScreen modules load failed: %s", e)
        # 加载失败后标记 loaded 且不重试：避免每次请求都重复尝试导入
        # （失败原因通常是 DLL 缺失/损坏，运行时不会自动恢复）
        _fastscreen_loaded = True
        _capture_engine = None
        _stream_manager = None


class FastScreenAdapter(FastScreenServicePort):
    """FastScreenServicePort 的 FastScreen 实现。

    通过 CaptureEngine 列出显示器/窗口，通过 StreamManager 管理多客户端共享捕获会话。
    当 ENABLE_FASTSCREEN=False 或 fastscreen.dll 加载失败时，is_available() 返回 False。
    """

    def __init__(self):
        self._bin_dir = _resolve_bin_dir()
        self._dll_path = self._bin_dir / "fastscreencore" / "fastscreen.dll"

        if ENABLE_FASTSCREEN:
            _load_fastsreen_modules()
            if self.is_available():
                _logger.info("FastScreen service initialized: dll=%s available=True", self._dll_path)
            else:
                _logger.warning(
                    "FastScreen service initialized but not available (dll exists=%s)",
                    self._dll_path.exists(),
                )
        else:
            _logger.info("FastScreen service disabled by config (ENABLE_FASTSCREEN=False)")

    def is_available(self) -> bool:
        """FastScreen 功能是否可用（配置启用 + DLL 存在 + 模块加载成功）。"""
        if not ENABLE_FASTSCREEN:
            return False
        if not self._dll_path.exists():
            return False
        return _capture_engine is not None and _stream_manager is not None

    def list_targets(self) -> dict:
        """列出所有可查看目标（显示器 + 窗口）。"""
        if not self.is_available():
            return {"disabled": not ENABLE_FASTSCREEN, "available": False, "monitors": [], "windows": []}

        try:
            monitors_raw = _capture_engine.enumerate_monitors()
            windows_raw = _capture_engine.enumerate_windows()
        except Exception as e:
            _logger.exception("FastScreen enumerate failed: %s", e)
            return {"disabled": False, "available": True, "monitors": [], "windows": [], "error": str(e)}

        monitors = [
            {"id": m.id, "name": m.name, "left": m.left, "top": m.top,
             "width": m.width, "height": m.height, "primary": bool(m.primary)}
            for m in monitors_raw
        ]
        windows = [
            {"hwnd": int(w.hwnd) if w.hwnd else 0, "title": w.title, "class_name": w.class_name,
             "left": w.left, "top": w.top, "width": w.width, "height": w.height, "visible": bool(w.visible)}
            for w in windows_raw
            if w.title and w.width > 0 and w.height > 0
        ]
        return {"disabled": False, "available": True, "monitors": monitors, "windows": windows}

    def get_status(self) -> dict:
        """返回服务状态。"""
        if not ENABLE_FASTSCREEN:
            return {"disabled": True, "available": False, "active_sessions": 0}
        available = self.is_available()
        active_sessions = 0
        if available and _stream_manager is not None:
            try:
                active_sessions = _stream_manager.session_count
            except Exception:
                active_sessions = 0
        return {"disabled": False, "available": available, "active_sessions": active_sessions}

    def cleanup(self) -> None:
        """daemon 退出时清理所有捕获会话。"""
        if _stream_manager is None:
            return
        try:
            stopped = _stream_manager.stop_all()
            _logger.info("FastScreen cleanup done (stopped %d sessions)", stopped)
        except Exception as e:
            _logger.warning("FastScreen cleanup failed: %s", e)
