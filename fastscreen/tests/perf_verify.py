"""
独立性能验证程序 - 对比修复前后的性能差异
测试项:
1. to_numpy() - 消除 ctypes.string_at() 中间拷贝
2. to_qimage() - 消除三重拷贝为双重拷贝
3. to_bytearray() - 优化 memmove 方式
4. enumerate_monitors/windows - 列表推导 vs append
"""
import sys
import os
import time
import ctypes
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastscreen._core import _Lib, MonitorInfo, WindowInfo, Frame, FRAME_CALLBACK
from fastscreen import CaptureEngine, CapturedFrame, TargetType, CaptureMethod

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from PySide6.QtGui import QImage
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


def bench(label, fn, iterations=50, warmup=5):
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
    p95 = sorted(times)[int(len(times) * 0.95)]
    return {"label": label, "avg": avg, "med": med, "min": mn, "p95": p95}


def print_comparison(before, after):
    label = before["label"]
    b_avg = before["avg"]
    a_avg = after["avg"]
    change = (a_avg - b_avg) / b_avg * 100
    speedup = b_avg / a_avg if a_avg > 0 else float('inf')
    print(f"  {label}:")
    print(f"    修复前 avg={b_avg:.3f}ms  med={before['med']:.3f}ms  p95={before['p95']:.3f}ms")
    print(f"    修复后 avg={a_avg:.3f}ms  med={after['med']:.3f}ms  p95={after['p95']:.3f}ms")
    print(f"    变化: {change:+.1f}%  加速比: {speedup:.2f}x")


def old_to_numpy(frame: CapturedFrame):
    size = frame._frame.stride * frame._frame.height
    raw = ctypes.string_at(frame._frame.data, size)
    arr = np.frombuffer(raw, dtype=np.uint8)
    return arr.reshape((frame._frame.height, frame._frame.width, frame._frame.bpp))


def new_to_numpy(frame: CapturedFrame):
    size = frame._frame.stride * frame._frame.height
    ptr = ctypes.cast(frame._frame.data, ctypes.POINTER(ctypes.c_uint8))
    arr = np.ctypeslib.as_array(ptr, shape=(size,))
    return arr.reshape((frame._frame.height, frame._frame.width, frame._frame.bpp))


def old_to_qimage(frame: CapturedFrame):
    size = frame._frame.stride * frame._frame.height
    raw = ctypes.string_at(frame._frame.data, size)
    qimg = QImage(raw, frame._frame.width, frame._frame.height,
                  frame._frame.stride, QImage.Format.Format_ARGB32)
    return qimg.copy()


def new_to_qimage(frame: CapturedFrame):
    size = frame._frame.stride * frame._frame.height
    raw = ctypes.string_at(frame._frame.data, size)
    qimg = QImage(raw, frame._frame.width, frame._frame.height,
                  frame._frame.stride, QImage.Format.Format_ARGB32)
    frame._buf = raw
    return qimg


def old_to_bytearray(frame: CapturedFrame):
    size = frame._frame.stride * frame._frame.height
    buf = bytearray(size)
    ctypes.memmove(ctypes.addressof((ctypes.c_uint8 * size).from_buffer(buf)),
                   frame._frame.data, size)
    return buf


def new_to_bytearray(frame: CapturedFrame):
    size = frame._frame.stride * frame._frame.height
    buf = bytearray(size)
    dest = (ctypes.c_uint8 * size).from_buffer(buf)
    ctypes.memmove(dest, frame._frame.data, size)
    return buf


def main():
    print("=" * 70)
    print("FastScreen 性能修复验证程序")
    print("=" * 70)

    engine = CaptureEngine()
    frame = engine.capture_monitor(0, method=CaptureMethod.BITBLT)
    if frame is None:
        print("ERROR: 无法截取屏幕，请确保有可用的显示器")
        return

    w, h = frame.width, frame.height
    print(f"\n测试帧: {w}x{h}, stride={frame.stride}, bpp={frame.bpp}")
    print(f"帧大小: {frame.stride * frame.height / 1024:.0f} KB\n")

    results = {}

    # --- Test 1: to_numpy ---
    if HAS_NUMPY:
        print("[1] to_numpy() 性能对比 (消除 ctypes.string_at 中间拷贝)")
        before = bench("to_numpy (旧)", lambda: old_to_numpy(frame), iterations=50)
        after = bench("to_numpy (新)", lambda: new_to_numpy(frame), iterations=50)
        print_comparison(before, after)
        results["to_numpy"] = (before, after)
        print()

    # --- Test 2: to_qimage ---
    if HAS_PYSIDE6:
        print("[2] to_qimage() 性能对比 (消除三重拷贝为双重拷贝)")
        before = bench("to_qimage (旧)", lambda: old_to_qimage(frame), iterations=50)
        after = bench("to_qimage (新)", lambda: new_to_qimage(frame), iterations=50)
        print_comparison(before, after)
        results["to_qimage"] = (before, after)
        print()

    # --- Test 3: to_bytearray ---
    print("[3] to_bytearray() 性能对比 (简化 from_buffer 调用)")
    before = bench("to_bytearray (旧)", lambda: old_to_bytearray(frame), iterations=50)
    after = bench("to_bytearray (新)", lambda: new_to_bytearray(frame), iterations=50)
    print_comparison(before, after)
    results["to_bytearray"] = (before, after)
    print()

    # --- Test 4: to_bytes ---
    print("[4] to_bytes() 基准 (无法优化，ctypes.string_at 是唯一方式)")
    r = bench("to_bytes", lambda: frame.to_bytes(), iterations=50)
    print(f"  to_bytes avg={r['avg']:.3f}ms  med={r['med']:.3f}ms")
    print()

    # --- Test 5: enumerate ---
    print("[5] enumerate_monitors 列表推导 vs append")
    monitors = engine.enumerate_monitors()

    def old_style():
        result = []
        for i in range(len(monitors)):
            result.append(monitors[i])
        return result

    def new_style():
        return [monitors[i] for i in range(len(monitors))]

    before = bench("enumerate append", old_style, iterations=1000)
    after = bench("enumerate 列表推导", new_style, iterations=1000)
    print_comparison(before, after)
    print()

    # --- Summary ---
    print("=" * 70)
    print("修复总结:")
    print("=" * 70)
    for name, (before, after) in results.items():
        speedup = before["avg"] / after["avg"] if after["avg"] > 0 else float('inf')
        change = (after["avg"] - before["avg"]) / before["avg"] * 100
        status = "✓ 提升" if change < -5 else ("≈ 持平" if abs(change) <= 5 else "✗ 退化")
        print(f"  {name}: {speedup:.2f}x ({change:+.1f}%) {status}")

    frame.release()

    print("\n验证完成。以上优化将应用到项目中。")


if __name__ == "__main__":
    main()
