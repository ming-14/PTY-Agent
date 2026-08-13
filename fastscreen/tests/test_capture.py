import pytest
from fastscreencore import CaptureEngine, CapturedFrame, TargetType, CaptureMethod, ErrorCode


class TestEnumerateMonitors:
    def test_returns_list(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        assert isinstance(monitors, list)

    def test_at_least_one_monitor(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        assert len(monitors) >= 1

    def test_monitor_has_required_fields(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        m = monitors[0]
        assert hasattr(m, "id")
        assert hasattr(m, "name")
        assert hasattr(m, "width")
        assert hasattr(m, "height")
        assert hasattr(m, "left")
        assert hasattr(m, "top")
        assert hasattr(m, "primary")

    def test_monitor_dimensions_positive(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        for m in monitors:
            assert m.width > 0
            assert m.height > 0

    def test_has_primary_monitor(self):
        engine = CaptureEngine()
        monitors = engine.enumerate_monitors()
        primary = [m for m in monitors if m.primary]
        assert len(primary) >= 1


class TestEnumerateWindows:
    def test_returns_list(self):
        engine = CaptureEngine()
        windows = engine.enumerate_windows()
        assert isinstance(windows, list)

    def test_at_least_some_windows(self):
        engine = CaptureEngine()
        windows = engine.enumerate_windows()
        assert len(windows) >= 1

    def test_window_has_required_fields(self):
        engine = CaptureEngine()
        windows = engine.enumerate_windows()
        w = windows[0]
        assert hasattr(w, "hwnd")
        assert hasattr(w, "title")
        assert hasattr(w, "class_name")
        assert hasattr(w, "width")
        assert hasattr(w, "height")
        assert hasattr(w, "visible")

    def test_window_hwnd_nonzero(self):
        engine = CaptureEngine()
        windows = engine.enumerate_windows()
        for w in windows:
            assert w.hwnd is not None
            if w.hwnd:
                assert int(w.hwnd) != 0


class TestCaptureMonitor:
    def test_auto_capture(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.AUTO)
        assert frame is not None
        assert frame.width > 0
        assert frame.height > 0
        assert frame.stride >= frame.width * frame.bpp
        frame.release()

    def test_bitblt_capture(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        assert frame.width > 0
        assert frame.height > 0
        frame.release()

    def test_dxgi_capture(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.DXGI)
        if frame is not None:
            assert frame.width > 0
            assert frame.height > 0
            frame.release()

    def test_wgc_monitor_capture(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.WGC)
        if frame is not None:
            assert frame.width > 0
            assert frame.height > 0
            frame.release()

    def test_invalid_monitor_id(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(999, method=CaptureMethod.BITBLT)
        assert frame is None

    def test_frame_data_readable(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        data = frame.to_bytes()
        assert len(data) == frame.stride * frame.height
        frame.release()


class TestCaptureWindow:
    def _get_visible_window(self):
        engine = CaptureEngine()
        windows = engine.enumerate_windows()
        for w in windows:
            if w.visible and w.width > 100 and w.height > 100:
                return int(w.hwnd)
        return None

    def test_bitblt_window_capture(self):
        hwnd = self._get_visible_window()
        if hwnd is None:
            pytest.skip("No visible window found")
        engine = CaptureEngine()
        frame = engine.capture_window(hwnd, method=CaptureMethod.BITBLT)
        assert frame is not None
        assert frame.width > 0
        assert frame.height > 0
        frame.release()

    def test_wgc_window_capture(self):
        hwnd = self._get_visible_window()
        if hwnd is None:
            pytest.skip("No visible window found")
        engine = CaptureEngine()
        frame = engine.capture_window(hwnd, method=CaptureMethod.WGC)
        if frame is not None:
            assert frame.width > 0
            assert frame.height > 0
            frame.release()

    def test_auto_window_capture(self):
        hwnd = self._get_visible_window()
        if hwnd is None:
            pytest.skip("No visible window found")
        engine = CaptureEngine()
        frame = engine.capture_window(hwnd, method=CaptureMethod.AUTO)
        assert frame is not None
        assert frame.width > 0
        frame.release()

    def test_dxgi_window_unsupported(self):
        hwnd = self._get_visible_window()
        if hwnd is None:
            pytest.skip("No visible window found")
        engine = CaptureEngine()
        frame = engine.capture_window(hwnd, method=CaptureMethod.DXGI)
        assert frame is None

    def test_invalid_hwnd(self):
        engine = CaptureEngine()
        frame = engine.capture_window(0, method=CaptureMethod.BITBLT)
        assert frame is None


class TestFrameSave:
    def test_save_png(self, tmp_path):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        path = str(tmp_path / "test.png")
        assert frame.save(path, format="png") is True
        import os
        assert os.path.getsize(path) > 0
        frame.release()

    def test_save_jpeg(self, tmp_path):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        path = str(tmp_path / "test.jpg")
        assert frame.save(path, format="jpeg") is True
        frame.release()

    def test_save_bmp(self, tmp_path):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        path = str(tmp_path / "test.bmp")
        assert frame.save(path, format="bmp") is True
        frame.release()


class TestContinuousCapture:
    def test_start_and_stop(self):
        engine = CaptureEngine()
        frames = []

        def on_frame(f):
            frames.append(f)

        ok = engine.start_continuous(
            TargetType.MONITOR, 0, on_frame,
            fps=10, method=CaptureMethod.BITBLT,
        )
        assert ok is True

        import time
        time.sleep(2)

        ok = engine.stop_continuous()
        assert ok is True
        assert len(frames) > 0

    def test_receive_valid_frames(self):
        engine = CaptureEngine()
        frames = []

        def on_frame(f):
            frames.append(f)

        engine.start_continuous(
            TargetType.MONITOR, 0, on_frame,
            fps=10, method=CaptureMethod.BITBLT,
        )
        import time
        time.sleep(1.5)
        engine.stop_continuous()

        assert len(frames) > 0
        for f in frames:
            assert f.width > 0
            assert f.height > 0

    def test_stop_without_start(self):
        engine = CaptureEngine()
        assert engine.stop_continuous() is False

    def test_restart_continuous(self):
        engine = CaptureEngine()
        frames1 = []
        frames2 = []

        engine.start_continuous(
            TargetType.MONITOR, 0, lambda f: frames1.append(f),
            fps=10, method=CaptureMethod.BITBLT,
        )
        import time
        time.sleep(0.5)
        engine.stop_continuous()
        count1 = len(frames1)

        engine.start_continuous(
            TargetType.MONITOR, 0, lambda f: frames2.append(f),
            fps=10, method=CaptureMethod.BITBLT,
        )
        time.sleep(0.5)
        engine.stop_continuous()
        count2 = len(frames2)

        assert count1 > 0
        assert count2 > 0

    def test_wgc_continuous_window(self):
        engine = CaptureEngine()
        windows = engine.enumerate_windows()
        target = None
        for w in windows:
            if w.visible and w.width > 100 and w.height > 100:
                target = w
                break
        if target is None:
            pytest.skip("No visible window")

        frames = []
        ok = engine.start_continuous(
            TargetType.WINDOW, int(target.hwnd), lambda f: frames.append(f),
            fps=10, method=CaptureMethod.WGC,
        )
        if not ok:
            pytest.skip("WGC continuous not available")

        import time
        time.sleep(2)
        engine.stop_continuous()
        assert len(frames) > 0
