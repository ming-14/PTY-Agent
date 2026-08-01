import ctypes
import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock
from fastscreen._core import (
    MonitorInfo, WindowInfo, Frame, ErrorCode, ImageFormat,
)
from fastscreen.capture import CapturedFrame, Monitor, Window


class TestMonitor:
    def test_from_info(self):
        info = MonitorInfo()
        info.id = 0
        info.name = "\\\\.\\DISPLAY1"
        info.left = 0
        info.top = 0
        info.width = 1920
        info.height = 1080
        info.primary = 1

        m = Monitor(info)
        assert m.id == 0
        assert m.name == "\\\\.\\DISPLAY1"
        assert m.left == 0
        assert m.top == 0
        assert m.width == 1920
        assert m.height == 1080
        assert m.primary is True

    def test_primary_false(self):
        info = MonitorInfo()
        info.primary = 0
        m = Monitor(info)
        assert m.primary is False

    def test_repr(self):
        info = MonitorInfo()
        info.id = 1
        info.name = "Test"
        info.width = 800
        info.height = 600
        info.left = 100
        info.top = 200
        m = Monitor(info)
        r = repr(m)
        assert "800x600" in r
        assert "Test" in r
        assert "(100,200)" in r


class TestWindow:
    def test_from_info(self):
        info = WindowInfo()
        info.hwnd = 0xABCD
        info.title = "Notepad"
        info.class_name = "Notepad"
        info.left = 10
        info.top = 20
        info.width = 800
        info.height = 600
        info.visible = 1

        w = Window(info)
        assert w.hwnd == 0xABCD
        assert w.title == "Notepad"
        assert w.class_name == "Notepad"
        assert w.left == 10
        assert w.top == 20
        assert w.width == 800
        assert w.height == 600
        assert w.visible is True

    def test_visible_false(self):
        info = WindowInfo()
        info.visible = 0
        w = Window(info)
        assert w.visible is False

    def test_repr(self):
        info = WindowInfo()
        info.hwnd = 0x1234
        info.title = "Test"
        info.width = 640
        info.height = 480
        w = Window(info)
        r = repr(w)
        assert "0x1234" in r
        assert "Test" in r
        assert "640x480" in r


class TestCapturedFrame:
    def _make_frame(self, width=4, height=4):
        frame = Frame()
        stride = width * 4
        size = stride * height
        buf = (ctypes.c_uint8 * size)()
        for i in range(size):
            buf[i] = (i * 37) & 0xFF

        frame.data = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8))
        frame.width = width
        frame.height = height
        frame.stride = stride
        frame.bpp = 4
        frame.timestamp_ms = 1234567890
        frame.owns_data = 0
        return frame, buf

    def test_properties(self):
        frame, buf = self._make_frame()
        cf = CapturedFrame(frame, owned=True)
        assert cf.width == 4
        assert cf.height == 4
        assert cf.stride == 16
        assert cf.bpp == 4
        assert cf.timestamp_ms == 1234567890

    def test_to_bytes(self):
        frame, buf = self._make_frame()
        cf = CapturedFrame(frame, owned=True)
        data = cf.to_bytes()
        assert len(data) == 4 * 4 * 4
        assert isinstance(data, bytes)

    def test_to_bytearray(self):
        frame, buf = self._make_frame()
        cf = CapturedFrame(frame, owned=True)
        data = cf.to_bytearray()
        assert len(data) == 4 * 4 * 4
        assert isinstance(data, bytearray)

    def test_release_idempotent(self):
        frame, buf = self._make_frame()
        cf = CapturedFrame(frame, owned=True)
        cf.release()
        cf.release()

    def test_release_non_owned(self):
        frame, buf = self._make_frame()
        cf = CapturedFrame(frame, owned=False)
        cf.release()

    def test_save_png(self):
        frame, buf = self._make_frame(64, 64)
        cf = CapturedFrame(frame, owned=True)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            result = cf.save(path, format="png")
            assert result is True
            assert os.path.getsize(path) > 0
        finally:
            os.unlink(path)

    def test_save_jpeg(self):
        frame, buf = self._make_frame(64, 64)
        cf = CapturedFrame(frame, owned=True)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            path = f.name
        try:
            result = cf.save(path, format="jpeg")
            assert result is True
            assert os.path.getsize(path) > 0
        finally:
            os.unlink(path)

    def test_save_bmp(self):
        frame, buf = self._make_frame(64, 64)
        cf = CapturedFrame(frame, owned=True)
        with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as f:
            path = f.name
        try:
            result = cf.save(path, format="bmp")
            assert result is True
            assert os.path.getsize(path) > 0
        finally:
            os.unlink(path)

    def test_save_invalid_format_defaults_png(self):
        frame, buf = self._make_frame(64, 64)
        cf = CapturedFrame(frame, owned=True)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            result = cf.save(path, format="unknown")
            assert result is True
        finally:
            os.unlink(path)

    def test_to_numpy_requires_numpy(self):
        frame, buf = self._make_frame()
        cf = CapturedFrame(frame, owned=True)
        try:
            arr = cf.to_numpy()
            import numpy as np
            assert isinstance(arr, np.ndarray)
            assert arr.shape == (4, 4, 4)
        except ImportError:
            pass

    def test_to_qimage_requires_pyside6(self):
        frame, buf = self._make_frame()
        cf = CapturedFrame(frame, owned=True)
        try:
            qimg = cf.to_qimage()
            from PySide6.QtGui import QImage
            assert isinstance(qimg, QImage)
            assert qimg.width() == 4
            assert qimg.height() == 4
        except ImportError:
            pass
