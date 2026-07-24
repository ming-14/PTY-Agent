"""
@file __init__.py
@brief cursorlocator — 鼠标光标圆环定位器

使用方式:
    import sys
    sys.path.insert(0, r'D:\\PTY-Agent\\src')
    import cursorlocator
    cursorlocator.start(outer_radius=16, inner_radius=8, alpha=90)
    cursorlocator.update_config(alpha=128)
    cursorlocator.stop()
"""

import threading

from .config import Config
from .ring_worker import MouseRingWindow

_instance = None
_thread = None
_lock = threading.Lock()


def start(**kwargs):
    """启动圆环定位器（后台线程，立即返回）。

    可选参数:
        outer_radius: 外圈半径 (默认 16)
        inner_radius: 内圈半径 (默认 8)
        alpha: 透明度 0-255 (默认 90)
        track_interval: 鼠标跟踪间隔 ms (默认 20)
        sample_interval: 取色间隔 ms (默认 100)
        timer_mode: 'auto' | 'custom' (默认 'auto')
        timer_multiplier: 自动模式倍率 (默认 1.0)
        timer_interval_custom: 自定义定时器间隔 ms (默认 16)
    """
    global _instance, _thread
    with _lock:
        if _instance is not None:
            return
        cfg = Config()
        if kwargs:
            cfg.set(**kwargs)
        _instance = _App(cfg)
        _thread = threading.Thread(
            target=_instance.run, daemon=True, name='CursorLocator')
        _thread.start()


def stop():
    """停止圆环定位器。"""
    global _instance, _thread
    with _lock:
        if _instance is None:
            return
        _instance.request_stop()
        _thread.join(timeout=3.0)
        _instance = None
        _thread = None


def update_config(**kwargs):
    """运行时修改配置参数。"""
    global _instance
    with _lock:
        if _instance is None:
            return
        _instance.cfg.set(**kwargs)


class _App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._ring = None

    def run(self):
        from .win32_api import (
            _GetMessageW, _TranslateMessage, _DispatchMessageW,
            byref, MSG, _PostQuitMessage, NULL,
        )

        self._ring = MouseRingWindow(self.cfg)
        self._ring.show()
        self._ring._update_frame(
            0, 0,
            self.cfg.get('outer_radius'),
            self.cfg.get('inner_radius'),
            self.cfg.get('alpha'))

        msg = MSG()
        while self._ring.running:
            ret = _GetMessageW(byref(msg), NULL, 0, 0)
            if ret <= 0:
                break
            _TranslateMessage(byref(msg))
            _DispatchMessageW(byref(msg))

        self._ring.cleanup()
        _PostQuitMessage(0)

    def request_stop(self):
        if self._ring:
            self._ring.running = False
            from .win32_api import _PostMessageW
            _PostMessageW(self._ring.hwnd, 0x0000, 0, 0)
