import pytest
import time
import os
import tempfile
from fastscreencore import CaptureEngine, CapturedFrame, TargetType, CaptureMethod, ErrorCode


class TestFullWorkflowMonitor:
    def test_enumerate_capture_save(self, tmp_path):
        engine = CaptureEngine()

        monitors = engine.enumerate_monitors()
        assert len(monitors) >= 1

        frame = engine.capture_monitor(monitors[0].id, method=CaptureMethod.AUTO)
        assert frame is not None
        assert frame.width == monitors[0].width
        assert frame.height == monitors[0].height

        path = str(tmp_path / "e2e_monitor.png")
        assert frame.save(path) is True
        assert os.path.getsize(path) > 0
        frame.release()

    def test_all_methods_monitor(self, tmp_path):
        engine = CaptureEngine()

        for method_name, method in [
            ("BitBlt", CaptureMethod.BITBLT),
            ("DXGI", CaptureMethod.DXGI),
            ("WGC", CaptureMethod.WGC),
        ]:
            frame = engine.capture_monitor(0, method=method)
            if frame is None:
                continue
            path = str(tmp_path / f"e2e_{method_name}.png")
            assert frame.save(path) is True
            assert os.path.getsize(path) > 0
            frame.release()


class TestFullWorkflowWindow:
    def _get_visible_window(self):
        engine = CaptureEngine()
        windows = engine.enumerate_windows()
        for w in windows:
            if w.visible and w.width > 100 and w.height > 100:
                return w
        return None

    def test_enumerate_capture_save_window(self, tmp_path):
        w = self._get_visible_window()
        if w is None:
            pytest.skip("No visible window")

        engine = CaptureEngine()
        frame = engine.capture_window(int(w.hwnd), method=CaptureMethod.AUTO)
        assert frame is not None

        path = str(tmp_path / "e2e_window.png")
        assert frame.save(path) is True
        assert os.path.getsize(path) > 0
        frame.release()


class TestFullWorkflowContinuous:
    def test_continuous_monitor_bitblt(self, tmp_path):
        engine = CaptureEngine()
        frames = []
        saved_count = [0]

        def on_frame(f):
            frames.append(f)
            if len(frames) == 1:
                path = str(tmp_path / "e2e_continuous.png")
                if f.save(path):
                    saved_count[0] += 1

        ok = engine.start_continuous(
            TargetType.MONITOR, 0, on_frame,
            fps=10, method=CaptureMethod.BITBLT,
        )
        assert ok is True

        time.sleep(2)
        engine.stop_continuous()

        assert len(frames) >= 1
        assert saved_count[0] >= 1

    def test_continuous_then_single(self):
        engine = CaptureEngine()

        frames = []
        engine.start_continuous(
            TargetType.MONITOR, 0, lambda f: frames.append(f),
            fps=10, method=CaptureMethod.BITBLT,
        )
        time.sleep(1)
        engine.stop_continuous()
        assert len(frames) > 0

        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        assert frame.width > 0
        frame.release()

    def test_continuous_wgc_window(self):
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
            fps=15, method=CaptureMethod.WGC,
        )
        if not ok:
            pytest.skip("WGC not available")

        time.sleep(2)
        engine.stop_continuous()
        assert len(frames) > 0


class TestFrameConversion:
    def test_to_bytes_and_back(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None

        data = frame.to_bytes()
        assert len(data) == frame.stride * frame.height

        arr = frame.to_bytearray()
        assert len(arr) == len(data)
        assert arr == bytearray(data)
        frame.release()

    def test_frame_timestamp(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        assert frame.timestamp_ms > 0
        frame.release()

    def test_frame_bpp(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        assert frame.bpp == 4
        frame.release()


class TestErrorHandling:
    def test_dxgi_window_returns_none(self):
        engine = CaptureEngine()
        windows = engine.enumerate_windows()
        if not windows:
            pytest.skip("No windows")
        frame = engine.capture_window(int(windows[0].hwnd), method=CaptureMethod.DXGI)
        assert frame is None

    def test_invalid_monitor_returns_none(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(9999, method=CaptureMethod.BITBLT)
        assert frame is None

    def test_zero_hwnd_returns_none(self):
        engine = CaptureEngine()
        frame = engine.capture_window(0, method=CaptureMethod.BITBLT)
        assert frame is None

    def test_capture_hint_for_dxgi_window(self):
        engine = CaptureEngine()
        windows = engine.enumerate_windows()
        if not windows:
            pytest.skip("No windows")
        frame, hint = engine._capture(
            TargetType.WINDOW, int(windows[0].hwnd), CaptureMethod.DXGI
        )
        assert frame is None
        assert "DXGI" in hint

    def test_multiple_engines(self):
        e1 = CaptureEngine()
        e2 = CaptureEngine()
        f1 = e1.capture_monitor(0, method=CaptureMethod.BITBLT)
        f2 = e2.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert f1 is not None
        assert f2 is not None
        f1.release()
        f2.release()


class TestPerformance:
    def test_bitblt_fps(self):
        engine = CaptureEngine()
        count = 30
        start = time.perf_counter()
        for _ in range(count):
            f = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
            if f:
                f.release()
        elapsed = time.perf_counter() - start
        fps = count / elapsed
        assert fps > 5

    def test_dxgi_fps(self):
        engine = CaptureEngine()
        f = engine.capture_monitor(0, method=CaptureMethod.DXGI)
        if f is None:
            pytest.skip("DXGI not available")
        f.release()

        count = 30
        start = time.perf_counter()
        for _ in range(count):
            f = engine.capture_monitor(0, method=CaptureMethod.DXGI)
            if f:
                f.release()
        elapsed = time.perf_counter() - start
        fps = count / elapsed
        assert fps > 10
