"""
@file ring_worker.py
@brief 环线窗口：MouseRingWindow + 定时器跟踪 + 渲染
"""

import math
import time
import ctypes

from .win32_api import *
from .config import Config
from .rendering import build_alpha_mask, render_ring_from_mask
from .pixel_color import get_pixel_color, compute_complement

_display_freq = 0


def _query_display_frequency() -> int:
    global _display_freq
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    try:
        if _EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, byref(dm)):
            freq = dm.dmDisplayFrequency
            if freq > 1:
                _display_freq = freq
                return freq
    except Exception:
        pass
    _display_freq = 0
    return 0


def _get_display_timer_interval() -> int:
    freq = _query_display_frequency()
    if freq > 0:
        return max(4, int(round(1000.0 / freq)))
    return 4


# ============================================================
# 环线窗口
# ============================================================
class MouseRingWindow:
    _instance = None

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.hwnd = None
        self.hdc_mem = None
        self.hbitmap = None
        self.bits_ptr = None
        self.running = True
        self._size = 0
        self._color = (173, 216, 230)
        self._ring_center_x = 0
        self._ring_center_y = 0
        self._last_sample_time = time.monotonic()
        self._current_track_ms = cfg.get('track_interval')

        self._alpha_mask = None
        self._last_shape_outer = 0
        self._last_shape_inner = 0
        self._last_frame_key = None
        self._prev_on_ring = False

        self._smooth_from_x = 0.0
        self._smooth_from_y = 0.0
        self._smooth_to_x = 0.0
        self._smooth_to_y = 0.0
        self._smooth_start_time = 0.0
        self._smooth_duration = 0.0
        self._last_track_time = 0.0

        self._current_timer_interval = 4

        MouseRingWindow._instance = self

        hinst = _GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(_ring_wnd_proc, c_void_p)
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = NULL
        wc.hCursor = NULL
        wc.hbrBackground = NULL
        wc.lpszMenuName = None
        wc.lpszClassName = 'MouseRingClass'
        wc.hIconSm = NULL

        atom = _RegisterClassExW(byref(wc))
        if atom == 0:
            raise RuntimeError('注册窗口类失败')

        cfg_snap = cfg.snapshot()
        self._size = cfg_snap['outer_radius'] * 3

        ex_style = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT
        self.hwnd = _CreateWindowExW(
            ex_style, 'MouseRingClass', 'CursorRing', WS_POPUP,
            0, 0, self._size, self._size, NULL, NULL, hinst, NULL,
        )
        if not self.hwnd:
            raise RuntimeError('创建窗口失败')

        _SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)

        self._init_drawing()
        self._start_timer_tracking()

    def _compute_timer_interval(self, snap=None) -> int:
        if snap is None:
            snap = self.cfg.snapshot()
        mode = snap.get('timer_mode', 'auto')
        if mode == 'custom':
            interval = max(4, snap.get('timer_interval_custom', 16))
        else:
            _query_display_frequency()
            freq = _display_freq
            if freq > 0:
                mult = snap.get('timer_multiplier', 1.0)
                interval = max(4, int(round(1000.0 / freq * mult)))
            else:
                interval = 4
        track_ms = snap.get('track_interval', 20)
        if interval > track_ms:
            interval = track_ms
        return interval

    def _start_timer_tracking(self):
        interval = self._compute_timer_interval()
        self._current_timer_interval = interval
        _SetTimer(self.hwnd, c_void_p(1), interval, NULL)

    def _stop_timer_tracking(self):
        _KillTimer(self.hwnd, c_void_p(1))

    def _init_drawing(self):
        s = self._size
        self.hdc_mem = _CreateCompatibleDC(NULL)
        if not self.hdc_mem:
            raise RuntimeError('创建内存 DC 失败')
        bmi = BITMAPV5HEADER()
        bmi.bV5Size = ctypes.sizeof(BITMAPV5HEADER)
        bmi.bV5Width = s
        bmi.bV5Height = -s
        bmi.bV5Planes = 1
        bmi.bV5BitCount = 32
        bmi.bV5Compression = BI_RGB
        bits = c_void_p()
        self.hbitmap = _CreateDIBSection(self.hdc_mem, byref(bmi),
                                         DIB_RGB_COLORS, byref(bits), NULL, 0)
        if not self.hbitmap:
            raise RuntimeError('创建 DIBSection 失败')
        self.bits_ptr = bits
        _SelectObject(self.hdc_mem, self.hbitmap)

    def _reinit_drawing(self):
        if self.hbitmap:
            _DeleteObject(self.hbitmap)
            self.hbitmap = None
        if self.hdc_mem:
            _DeleteDC(self.hdc_mem)
            self.hdc_mem = None
        self._init_drawing()

    def _resize(self, new_outer):
        new_size = new_outer * 3
        if new_size != self._size:
            self._size = new_size
            self._alpha_mask = None
            _SetWindowPos(self.hwnd, NULL, 0, 0, new_size, new_size,
                          SWP_NOMOVE | SWP_NOACTIVATE)
            self._reinit_drawing()

    def _update_frame(self, mouse_x, mouse_y, outer_r, inner_r, user_alpha):
        _SetWindowPos(self.hwnd, c_void_p(-1), 0, 0, 0, 0,
                      SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

        cr, cg, cb = self._color

        if (self._alpha_mask is None
                or self._last_shape_outer != outer_r
                or self._last_shape_inner != inner_r):
            self._alpha_mask = build_alpha_mask(self._size, outer_r, inner_r)
            self._last_shape_outer = outer_r
            self._last_shape_inner = inner_r

        buf = render_ring_from_mask(self._alpha_mask, self._size, cr, cg, cb, user_alpha)

        arr = (ctypes.c_ubyte * len(buf)).from_buffer(buf)
        ctypes.memmove(self.bits_ptr, arr, len(buf))

        dst = POINT(mouse_x - self._size // 2, mouse_y - self._size // 2)
        sz = SIZE(self._size, self._size)
        src = POINT(0, 0)
        bf = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)
        _UpdateLayeredWindow(self.hwnd, NULL, byref(dst), byref(sz),
                             self.hdc_mem, byref(src), 0, byref(bf), ULW_ALPHA)

    def _on_timer(self):
        now = time.monotonic()
        snap = self.cfg.snapshot()

        if snap['force_refresh']:
            self._last_sample_time = 0
            self.cfg.set(force_refresh=False)

        if snap['track_interval'] != self._current_track_ms:
            self._current_track_ms = snap['track_interval']

        new_timer = self._compute_timer_interval(snap)
        if new_timer != self._current_timer_interval:
            self._current_timer_interval = new_timer
            _KillTimer(self.hwnd, c_void_p(1))
            _SetTimer(self.hwnd, c_void_p(1), new_timer, NULL)

        want = snap['outer_radius'] * 3
        if want != self._size:
            self._resize(snap['outer_radius'])
            self._last_frame_key = None

        # 阶段1: 按跟踪频率读取鼠标位置
        if (now - self._last_track_time) * 1000 >= self._current_track_ms:
            self._last_track_time = now
            pt = POINT()
            _GetCursorPos(byref(pt))

            if self._current_timer_interval >= self._current_track_ms * 0.8:
                self._ring_center_x = pt.x
                self._ring_center_y = pt.y
                self._smooth_from_x = float(pt.x)
                self._smooth_from_y = float(pt.y)
                self._smooth_to_x = float(pt.x)
                self._smooth_to_y = float(pt.y)
                self._smooth_start_time = now
                self._smooth_duration = 0.0
            else:
                self._smooth_from_x = float(self._ring_center_x)
                self._smooth_from_y = float(self._ring_center_y)
                self._smooth_to_x = float(pt.x)
                self._smooth_to_y = float(pt.y)
                self._smooth_start_time = now
                self._smooth_duration = self._current_track_ms / 1000.0

            self._on_sample(pt.x, pt.y, snap)

        # 阶段2: 匀速线性插值
        elapsed = now - self._smooth_start_time
        if (self._smooth_duration > 0 and elapsed >= 0.001
                and elapsed < self._smooth_duration):
            t = elapsed / self._smooth_duration
            disp_x = self._smooth_from_x + (self._smooth_to_x - self._smooth_from_x) * t
            disp_y = self._smooth_from_y + (self._smooth_to_y - self._smooth_from_y) * t
        else:
            if elapsed >= self._smooth_duration:
                disp_x = self._smooth_to_x
                disp_y = self._smooth_to_y
            else:
                disp_x = float(self._ring_center_x)
                disp_y = float(self._ring_center_y)

        self._ring_center_x = int(round(disp_x))
        self._ring_center_y = int(round(disp_y))

        # 阶段3: 渲染
        frame_key = (self._ring_center_x, self._ring_center_y, self._color, snap['alpha'])
        if frame_key == self._last_frame_key:
            return
        self._last_frame_key = frame_key

        self._update_frame(self._ring_center_x, self._ring_center_y,
                           snap['outer_radius'], snap['inner_radius'], snap['alpha'])

    def _on_sample(self, mouse_x, mouse_y, snap):
        now = time.monotonic()
        if (now - self._last_sample_time) * 1000 < snap['sample_interval']:
            return
        self._last_sample_time = now

        dx = mouse_x - self._ring_center_x
        dy = mouse_y - self._ring_center_y
        dist = math.sqrt(dx * dx + dy * dy)
        on_ring = snap['outer_radius'] > dist >= snap['inner_radius']

        if on_ring != self._prev_on_ring:
            self._prev_on_ring = on_ring
            self.cfg.set(on_ring=on_ring)

        if not on_ring:
            pixel = get_pixel_color(mouse_x, mouse_y)
            comp = compute_complement(pixel)
            self._color = comp
            self.cfg.set(last_pixel_color=pixel,
                         last_complement_color=comp)

    def show(self):
        _SetWindowPos(self.hwnd, c_void_p(-1), 0, 0, 0, 0,
                      SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def cleanup(self):
        self._stop_timer_tracking()
        if self.hbitmap:
            _DeleteObject(self.hbitmap)
            self.hbitmap = None
        if self.hdc_mem:
            _DeleteDC(self.hdc_mem)
            self.hdc_mem = None
        if self.hwnd:
            _DestroyWindow(self.hwnd)
            self.hwnd = None


# ============================================================
# Win32 窗口过程
# ============================================================
@WNDPROC
def _ring_wnd_proc(hwnd, msg, wparam, lparam):
    win = MouseRingWindow._instance
    if win is None:
        return _DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == WM_TIMER:
        win._on_timer()
        return 0
    elif msg == WM_DESTROY:
        win.running = False
        return 0
    elif msg == WM_PAINT:
        ps = PAINTSTRUCT()
        _BeginPaint(hwnd, byref(ps))
        _EndPaint(hwnd, byref(ps))
        return 0
    return _DefWindowProcW(hwnd, msg, wparam, lparam)
