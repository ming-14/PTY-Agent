import ctypes
import pytest
from fastscreen._core import (
    MonitorInfo, WindowInfo, Frame, FRAME_CALLBACK,
    TargetType, CaptureMethod, ImageFormat, ErrorCode, _Lib,
)


class TestMonitorInfo:
    def test_fields_exist(self):
        info = MonitorInfo()
        assert hasattr(info, "id")
        assert hasattr(info, "name")
        assert hasattr(info, "left")
        assert hasattr(info, "top")
        assert hasattr(info, "width")
        assert hasattr(info, "height")
        assert hasattr(info, "primary")

    def test_default_values(self):
        info = MonitorInfo()
        assert info.id == 0
        assert info.name == ""
        assert info.left == 0
        assert info.top == 0
        assert info.width == 0
        assert info.height == 0
        assert info.primary == 0

    def test_set_values(self):
        info = MonitorInfo()
        info.id = 1
        info.name = "Test Monitor"
        info.left = 1920
        info.top = 0
        info.width = 1920
        info.height = 1080
        info.primary = 1
        assert info.id == 1
        assert info.name == "Test Monitor"
        assert info.width == 1920
        assert info.height == 1080
        assert info.primary == 1

    def test_size(self):
        expected = ctypes.sizeof(MonitorInfo)
        assert expected > 0


class TestWindowInfo:
    def test_fields_exist(self):
        info = WindowInfo()
        assert hasattr(info, "hwnd")
        assert hasattr(info, "title")
        assert hasattr(info, "class_name")
        assert hasattr(info, "left")
        assert hasattr(info, "top")
        assert hasattr(info, "width")
        assert hasattr(info, "height")
        assert hasattr(info, "visible")

    def test_default_values(self):
        info = WindowInfo()
        assert info.hwnd is None or info.hwnd == 0
        assert info.title == ""
        assert info.class_name == ""
        assert info.visible == 0

    def test_set_values(self):
        info = WindowInfo()
        info.hwnd = 0x12345
        info.title = "Test Window"
        info.class_name = "TestClass"
        info.width = 800
        info.height = 600
        info.visible = 1
        assert info.hwnd == 0x12345
        assert info.title == "Test Window"
        assert info.visible == 1


class TestFrame:
    def test_fields_exist(self):
        frame = Frame()
        assert hasattr(frame, "data")
        assert hasattr(frame, "width")
        assert hasattr(frame, "height")
        assert hasattr(frame, "stride")
        assert hasattr(frame, "bpp")
        assert hasattr(frame, "timestamp_ms")
        assert hasattr(frame, "owns_data")

    def test_default_values(self):
        frame = Frame()
        assert frame.width == 0
        assert frame.height == 0
        assert frame.stride == 0
        assert frame.bpp == 0
        assert frame.timestamp_ms == 0
        assert frame.owns_data == 0


class TestConstants:
    def test_target_type(self):
        assert TargetType.MONITOR == 0
        assert TargetType.WINDOW == 1

    def test_capture_method(self):
        assert CaptureMethod.AUTO == 0
        assert CaptureMethod.DXGI == 1
        assert CaptureMethod.WGC == 2
        assert CaptureMethod.BITBLT == 3

    def test_image_format(self):
        assert ImageFormat.PNG == 0
        assert ImageFormat.JPEG == 1
        assert ImageFormat.BMP == 2

    def test_error_code(self):
        assert ErrorCode.OK == 0
        assert ErrorCode.NOT_INITIALIZED == -1
        assert ErrorCode.INVALID_PARAM == -2
        assert ErrorCode.CAPTURE_FAILED == -3
        assert ErrorCode.ENCODE_FAILED == -4
        assert ErrorCode.NO_OUTPUT == -5
        assert ErrorCode.UNSUPPORTED == -6
        assert ErrorCode.ALREADY_RUNNING == -7
        assert ErrorCode.NOT_RUNNING == -8


class TestFrameCallback:
    def test_callback_type(self):
        called = [False]

        @FRAME_CALLBACK
        def my_callback(frame_ptr, user_data):
            called[0] = True

        assert callable(my_callback)
        assert not called[0]

    def test_callback_invocable(self):
        frame = Frame()
        frame.width = 100
        frame.height = 50
        frame.stride = 400
        frame.bpp = 4
        frame.owns_data = 0

        received = [None]

        @FRAME_CALLBACK
        def my_callback(frame_ptr, user_data):
            received[0] = frame_ptr.contents.width

        my_callback(ctypes.byref(frame), None)
        assert received[0] == 100


class TestLib:
    def test_lib_singleton(self):
        lib1 = _Lib.get()
        lib2 = _Lib.get()
        assert lib1 is lib2

    def test_lib_has_fs_init(self):
        lib = _Lib.get()
        assert hasattr(lib.lib, "fs_init")

    def test_lib_has_all_functions(self):
        lib = _Lib.get()
        funcs = [
            "fs_init", "fs_shutdown",
            "fs_enum_monitors", "fs_enum_windows", "fs_free_enum",
            "fs_create_session", "fs_destroy_session",
            "fs_capture_frame", "fs_frame_save",
            "fs_frame_copy_data", "fs_frame_release",
            "fs_start_continuous", "fs_stop_continuous",
        ]
        for func in funcs:
            assert hasattr(lib.lib, func), f"Missing function: {func}"
