import pytest
import time
import os
import tempfile
import statistics
from fastscreencore import CaptureEngine, CapturedFrame, TargetType, CaptureMethod, ErrorCode


def bench(label, fn, iterations=20, warmup=3):
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)
    avg = statistics.mean(times)
    med = statistics.median(times)
    mn = min(times)
    mx = max(times)
    p95 = sorted(times)[int(len(times) * 0.95)]
    print(f"\n[BENCH] {label}:")
    print(f"  avg={avg:.2f}ms  med={med:.2f}ms  min={mn:.2f}ms  max={mx:.2f}ms  p95={p95:.2f}ms  (n={iterations})")
    return {"avg": avg, "med": med, "min": mn, "max": mx, "p95": p95, "n": iterations}


class TestBenchSingleCapture:
    def test_bench_bitblt_single(self):
        engine = CaptureEngine()
        def do():
            f = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
            if f:
                f.release()
        r = bench("BitBlt 单帧截图", do, iterations=30)
        assert r["avg"] > 0

    def test_bench_dxgi_single(self):
        engine = CaptureEngine()
        f = engine.capture_monitor(0, method=CaptureMethod.DXGI)
        if f is None:
            pytest.skip("DXGI 不可用")
        f.release()
        def do():
            f = engine.capture_monitor(0, method=CaptureMethod.DXGI)
            if f:
                f.release()
        r = bench("DXGI 单帧截图", do, iterations=30)
        assert r["avg"] > 0

    def test_bench_wgc_single(self):
        engine = CaptureEngine()
        f = engine.capture_monitor(0, method=CaptureMethod.WGC)
        if f is None:
            pytest.skip("WGC 不可用")
        f.release()
        def do():
            f = engine.capture_monitor(0, method=CaptureMethod.WGC)
            if f:
                f.release()
        r = bench("WGC 单帧截图", do, iterations=20)
        assert r["avg"] > 0

    def test_bench_auto_single(self):
        engine = CaptureEngine()
        def do():
            f = engine.capture_monitor(0, method=CaptureMethod.AUTO)
            if f:
                f.release()
        r = bench("Auto 单帧截图", do, iterations=30)
        assert r["avg"] > 0


class TestBenchFrameConversion:
    def test_bench_to_bytes(self):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        def do():
            _ = frame.to_bytes()
        r = bench("CapturedFrame.to_bytes()", do, iterations=20)
        frame.release()

    def test_bench_to_numpy(self):
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy 未安装")
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        def do():
            _ = frame.to_numpy()
        r = bench("CapturedFrame.to_numpy()", do, iterations=20)
        frame.release()

    def test_bench_to_qimage(self):
        try:
            from PySide6.QtGui import QImage
        except ImportError:
            pytest.skip("PySide6 未安装")
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        def do():
            _ = frame.to_qimage()
        r = bench("CapturedFrame.to_qimage()", do, iterations=20)
        frame.release()


class TestBenchImageEncode:
    def test_bench_save_png(self, tmp_path):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        path = str(tmp_path / "bench.png")
        def do():
            frame.save(path, format="png")
        r = bench("保存 PNG", do, iterations=10)
        frame.release()

    def test_bench_save_jpeg(self, tmp_path):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        path = str(tmp_path / "bench.jpg")
        def do():
            frame.save(path, format="jpeg")
        r = bench("保存 JPEG", do, iterations=10)
        frame.release()

    def test_bench_save_bmp(self, tmp_path):
        engine = CaptureEngine()
        frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
        assert frame is not None
        path = str(tmp_path / "bench.bmp")
        def do():
            frame.save(path, format="bmp")
        r = bench("保存 BMP", do, iterations=10)
        frame.release()


class TestBenchContinuous:
    def test_bench_continuous_bitblt_throughput(self):
        engine = CaptureEngine()
        frames = []
        def on_frame(f):
            frames.append(f)
        ok = engine.start_continuous(
            TargetType.MONITOR, 0, on_frame,
            fps=60, method=CaptureMethod.BITBLT,
        )
        assert ok
        time.sleep(3)
        engine.stop_continuous()
        fps = len(frames) / 3.0
        print(f"\n[BENCH] 连续捕获 BitBlt 60fps: 收到 {len(frames)} 帧 / 3s = {fps:.1f} fps")
        for f in frames:
            try:
                f.release()
            except Exception:
                pass

    def test_bench_continuous_dxgi_throughput(self):
        engine = CaptureEngine()
        f = engine.capture_monitor(0, method=CaptureMethod.DXGI)
        if f is None:
            pytest.skip("DXGI 不可用")
        f.release()

        frames = []
        def on_frame(f):
            frames.append(f)
        ok = engine.start_continuous(
            TargetType.MONITOR, 0, on_frame,
            fps=60, method=CaptureMethod.DXGI,
        )
        assert ok
        time.sleep(3)
        engine.stop_continuous()
        fps = len(frames) / 3.0
        print(f"\n[BENCH] 连续捕获 DXGI 60fps: 收到 {len(frames)} 帧 / 3s = {fps:.1f} fps")
        for f in frames:
            try:
                f.release()
            except Exception:
                pass


class TestBenchEnumeration:
    def test_bench_enum_monitors(self):
        engine = CaptureEngine()
        def do():
            _ = engine.enumerate_monitors()
        r = bench("枚举显示器", do, iterations=20)

    def test_bench_enum_windows(self):
        engine = CaptureEngine()
        def do():
            _ = engine.enumerate_windows()
        r = bench("枚举窗口", do, iterations=20)
