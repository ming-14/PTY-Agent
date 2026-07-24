from __future__ import annotations

import ctypes
import logging
import time
from typing import Optional, List, Callable
from ._core import (
    _Lib, MonitorInfo, WindowInfo, Frame, FRAME_CALLBACK,
    TargetType, CaptureMethod, ImageFormat, ErrorCode,
)

logger = logging.getLogger("fastscreen")

_METHOD_NAMES = {CaptureMethod.AUTO: "Auto", CaptureMethod.DXGI: "DXGI",
                 CaptureMethod.WGC: "WGC", CaptureMethod.BITBLT: "BitBlt"}
_TARGET_NAMES = {TargetType.MONITOR: "monitor", TargetType.WINDOW: "window"}
_ERR_NAMES = {v: k for k, v in vars(ErrorCode).items() if not k.startswith('_')}


def encode_bgra_to_image(bgra_data: bytes, width: int, height: int, stride: int,
                         format: str = "jpeg", quality: float = 0.8,
                         scale_width: int = 0, scale_height: int = 0) -> bytes:
    fmt_map = {"png": ImageFormat.PNG, "jpeg": ImageFormat.JPEG, "jpg": ImageFormat.JPEG, "bmp": ImageFormat.BMP}
    img_fmt = fmt_map.get(format.lower(), ImageFormat.JPEG)

    lib = _Lib.get()

    # 用 c_char_p 直接引用 bytes 内部缓冲区，避免 ctypes.cast(c_ubyte_Array, POINTER) 创建
    # b_objects dict 循环引用（详见 encoding/mjpeg.py 的注释）。
    c_ptr = ctypes.c_char_p(bgra_data)

    frame = Frame()
    frame.width = width
    frame.height = height
    frame.stride = stride
    frame.bpp = 4
    frame.format = 0
    frame.data = ctypes.cast(c_ptr, ctypes.POINTER(ctypes.c_uint8))
    frame.owns_data = 0
    frame.timestamp_ms = 0

    buf_ptr = ctypes.POINTER(ctypes.c_uint8)()
    out_size = ctypes.c_int()

    if scale_width > 0 and scale_height > 0:
        result = lib.lib.fs_frame_encode_to_buffer_scaled(
            ctypes.byref(frame), img_fmt, quality,
            scale_width, scale_height,
            ctypes.byref(buf_ptr), ctypes.byref(out_size),
        )
    else:
        result = lib.lib.fs_frame_encode_to_buffer(
            ctypes.byref(frame), img_fmt, quality,
            ctypes.byref(buf_ptr), ctypes.byref(out_size),
        )

    if result != 0 or not buf_ptr or out_size.value <= 0:
        return b""

    try:
        return ctypes.string_at(buf_ptr, out_size.value)
    finally:
        lib.lib.fs_free_buffer(buf_ptr)


class CapturedFrame:
    __slots__ = ("_frame", "_owned", "_buf")

    def __init__(self, frame: Frame, owned: bool = True):
        self._frame = frame
        self._owned = owned
        self._buf = None

    @property
    def width(self) -> int:
        return self._frame.width

    @property
    def height(self) -> int:
        return self._frame.height

    @property
    def stride(self) -> int:
        return self._frame.stride

    @property
    def bpp(self) -> int:
        return self._frame.bpp

    @property
    def timestamp_ms(self) -> int:
        return self._frame.timestamp_ms

    def to_bytes(self) -> bytes:
        size = self._frame.stride * self._frame.height
        return ctypes.string_at(self._frame.data, size)

    def to_bytes_gil_safe(self) -> bytes:
        """GIL-safe 版本的 to_bytes()。

        使用 fs_frame_copy_data（C 函数）拷贝帧数据。ctypes 调用 C 函数时会释放 GIL，
        即使 memcpy 卡住（GPU 驱动死锁 / WGC 纹理内存失效），也不会阻塞 Python event loop。

        与 to_bytes() 的区别：
        - to_bytes()        → ctypes.string_at → PyBytes_FromStringAndSize + memcpy，持有 GIL
        - to_bytes_gil_safe → fs_frame_copy_data → C 端 memcpy，释放 GIL

        实现说明：直接用 (c_uint8 * size)() 创建空数组让 C 函数写入，避免 from_buffer(bytearray)
        产生的中间 bytearray 分配。直接创建的数组 b_objects 为空，引用计数管理更轻量。
        """
        size = self._frame.stride * self._frame.height
        if size <= 0 or not self._frame.data:
            return b""
        # 直接创建空数组，C 函数 fs_frame_copy_data 写入数据
        dst = (ctypes.c_uint8 * size)()
        result = _Lib.get().lib.fs_frame_copy_data(
            ctypes.byref(self._frame),
            dst,
            size,
        )
        # fs_frame_copy_data 返回拷贝的字节数（正数=成功），负数=错误码
        if result < 0:
            logger.warning("fs_frame_copy_data failed: %d", result)
            return b""
        return bytes(dst)

    def to_bytearray(self) -> bytearray:
        size = self._frame.stride * self._frame.height
        buf = bytearray(size)
        ctypes.memmove(ctypes.addressof((ctypes.c_uint8 * size).from_buffer(buf)),
                       self._frame.data, size)
        return buf

    def to_numpy(self):
        try:
            import numpy as np
        except ImportError:
            raise ImportError("numpy is required for to_numpy()")

        buf = self.to_bytearray()
        height = self._frame.height
        stride = self._frame.stride
        width = self._frame.width
        bpp = self._frame.bpp

        arr = np.frombuffer(buf, dtype=np.uint8).reshape((height, stride))
        self._buf = buf
        if stride == width * bpp:
            return arr.reshape((height, width, bpp))
        return np.ascontiguousarray(arr[:, :width * bpp]).reshape((height, width, bpp))

    def to_qimage(self):
        try:
            from PySide6.QtGui import QImage
        except ImportError:
            raise ImportError("PySide6 is required for to_qimage()")

        size = self._frame.stride * self._frame.height
        raw = ctypes.string_at(self._frame.data, size)
        self._buf = raw
        qimg = QImage(raw, self._frame.width, self._frame.height, self._frame.stride, QImage.Format.Format_ARGB32)
        return qimg

    def save(self, path: str, format: str = "png", quality: float = 0.9) -> bool:
        fmt_map = {"png": ImageFormat.PNG, "jpeg": ImageFormat.JPEG, "jpg": ImageFormat.JPEG, "bmp": ImageFormat.BMP}
        img_fmt = fmt_map.get(format.lower(), ImageFormat.PNG)

        lib = _Lib.get()
        result = lib.lib.fs_frame_save(
            ctypes.byref(self._frame), path, img_fmt, quality
        )
        return result == 0

    def to_image_bytes(self, format: str = "jpeg", quality: float = 0.8, width: int = 0, height: int = 0) -> bytes:
        fmt_map = {"png": ImageFormat.PNG, "jpeg": ImageFormat.JPEG, "jpg": ImageFormat.JPEG, "bmp": ImageFormat.BMP}
        img_fmt = fmt_map.get(format.lower(), ImageFormat.JPEG)

        lib = _Lib.get()
        buf_ptr = ctypes.POINTER(ctypes.c_uint8)()
        size = ctypes.c_int()

        if width > 0 and height > 0:
            result = lib.lib.fs_frame_encode_to_buffer_scaled(
                ctypes.byref(self._frame), img_fmt, quality,
                width, height,
                ctypes.byref(buf_ptr), ctypes.byref(size),
            )
        else:
            result = lib.lib.fs_frame_encode_to_buffer(
                ctypes.byref(self._frame), img_fmt, quality,
                ctypes.byref(buf_ptr), ctypes.byref(size),
            )

        if result != 0 or not buf_ptr or size.value <= 0:
            return b""

        try:
            return ctypes.string_at(buf_ptr, size.value)
        finally:
            lib.lib.fs_free_buffer(buf_ptr)

    def release(self):
        if self._owned and self._frame.owns_data:
            _Lib.get().lib.fs_frame_release(ctypes.byref(self._frame))
            self._frame.owns_data = 0
            self._owned = False

    def __del__(self):
        self.release()


class Monitor:
    __slots__ = ("id", "name", "left", "top", "width", "height", "primary")

    def __init__(self, info: MonitorInfo):
        self.id = info.id
        self.name = info.name
        self.left = info.left
        self.top = info.top
        self.width = info.width
        self.height = info.height
        self.primary = bool(info.primary)

    def __repr__(self):
        return f"Monitor(id={self.id}, name={self.name!r}, {self.width}x{self.height} at ({self.left},{self.top}))"


class Window:
    __slots__ = ("hwnd", "title", "class_name", "left", "top", "width", "height", "visible")

    def __init__(self, info: WindowInfo):
        self.hwnd = info.hwnd
        self.title = info.title
        self.class_name = info.class_name
        self.left = info.left
        self.top = info.top
        self.width = info.width
        self.height = info.height
        self.visible = bool(info.visible)

    def __repr__(self):
        return f"Window(hwnd={self.hwnd:#x}, title={self.title!r}, {self.width}x{self.height})"


class CaptureEngine:
    def __init__(self):
        self._lib = _Lib.get()
        self._session_id: Optional[int] = None
        self._continuous_callback = None

    def enumerate_monitors(self) -> List[Monitor]:
        ptr = ctypes.POINTER(MonitorInfo)()
        count = ctypes.c_int()

        result = self._lib.lib.fs_enum_monitors(ctypes.byref(ptr), ctypes.byref(count))
        if result != 0:
            return []

        monitors = []
        for i in range(count.value):
            monitors.append(Monitor(ptr[i]))

        self._lib.lib.fs_free_enum(ptr)
        return monitors

    def enumerate_windows(self) -> List[Window]:
        ptr = ctypes.POINTER(WindowInfo)()
        count = ctypes.c_int()

        result = self._lib.lib.fs_enum_windows(ctypes.byref(ptr), ctypes.byref(count))
        if result != 0:
            return []

        windows = []
        for i in range(count.value):
            windows.append(Window(ptr[i]))

        self._lib.lib.fs_free_enum(ptr)
        return windows

    def capture_monitor(self, monitor_id: int = 0, method: int = CaptureMethod.AUTO) -> Optional[CapturedFrame]:
        frame, _ = self._capture(TargetType.MONITOR, monitor_id, method)
        return frame

    def capture_window(self, hwnd: int, method: int = CaptureMethod.AUTO) -> Optional[CapturedFrame]:
        frame, _ = self._capture(TargetType.WINDOW, hwnd, method)
        return frame

    def _capture(self, target_type: int, target_id: int, method: int) -> tuple:
        frame = Frame()
        result = self._lib.lib.fs_capture_frame(
            0, target_type, target_id, method, ctypes.byref(frame)
        )
        if result != 0:
            err_name = _ERR_NAMES.get(result, f"UNKNOWN({result})")
            logger.error("capture failed: %s %s method=%s error=%s",
                         _TARGET_NAMES.get(target_type, str(target_type)),
                         target_id, _METHOD_NAMES.get(method, str(method)), err_name)
            hint = ""
            if method == CaptureMethod.WGC and result == int(ErrorCode.UNSUPPORTED):
                if target_type == TargetType.WINDOW:
                    hint = "该窗口不支持 WGC 截屏（UWP/系统窗口等），请使用 BitBlt 或 Auto"
                else:
                    hint = "该系统不支持 WGC 截屏，请使用 DXGI 或 BitBlt"
            elif method == CaptureMethod.WGC and result == int(ErrorCode.NO_OUTPUT):
                hint = "WGC 截屏超时（窗口过小或未渲染），请使用 BitBlt 或 Auto"
            elif method == CaptureMethod.DXGI and target_type == TargetType.WINDOW:
                hint = "DXGI 不支持窗口截屏，请使用 WGC 或 BitBlt"
            elif method == CaptureMethod.DXGI and result == int(ErrorCode.NO_OUTPUT):
                hint = "DXGI Desktop Duplication 不可用（可能已被其他程序占用）"
            return None, hint
        return CapturedFrame(frame, owned=True), ""

    def start_continuous(
        self,
        target_type: int,
        target_id: int,
        callback: Callable[[CapturedFrame], None],
        fps: int = 60,
        method: int = CaptureMethod.AUTO,
    ) -> bool:
        if self._session_id is not None:
            self.stop_continuous()

        self._session_id = self._lib.lib.fs_create_session(target_type, target_id, method)
        if self._session_id <= 0:
            self._session_id = None
            logger.error("start_continuous: create_session failed")
            return False

        self._continuous_callback = FRAME_CALLBACK(
            lambda frame_ptr, user_data: self._on_frame(frame_ptr, callback)
        )

        result = self._lib.lib.fs_start_continuous(
            self._session_id, target_type, target_id, method, fps,
            self._continuous_callback, None,
        )
        if result != 0:
            logger.error("start_continuous: fs_start_continuous failed err=%d", result)
            return False
        return True

    def stop_continuous(self) -> bool:
        if self._session_id is None:
            return False

        result = self._lib.lib.fs_stop_continuous(self._session_id)
        self._lib.lib.fs_destroy_session(self._session_id)
        self._session_id = None
        self._continuous_callback = None
        return result == 0

    def is_running(self) -> bool:
        if self._session_id is None:
            return False
        return self._lib.lib.fs_session_is_running(self._session_id) != 0

    def _on_frame(self, frame_ptr, user_callback: Callable):
        try:
            src = frame_ptr.contents
            size = src.stride * src.height
            if size <= 0 or not src.data:
                if src.owns_data:
                    self._lib.lib.fs_frame_release(frame_ptr)
                return

            frame = Frame()
            ctypes.memmove(ctypes.addressof(frame), frame_ptr, ctypes.sizeof(Frame))

            cf = CapturedFrame(frame, owned=bool(src.owns_data))

            if src.owns_data:
                frame_ptr.contents.owns_data = 0
                frame_ptr.contents.data = None

            user_callback(cf)
        except Exception as e:
            logger.error("continuous frame callback error: %s", e)

    def __del__(self):
        if self._session_id is not None:
            try:
                self.stop_continuous()
            except Exception:
                pass
