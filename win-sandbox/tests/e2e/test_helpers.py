"""test_helpers.py - Python helpers 单元测试

测试：
  - contains_access_denied_keyword 工具函数
  - read_pipe / write_pipe 读写正确
  - wait_process / close_handle
  - WallClockTimer 超时触发 terminate
  - StatsPoller 周期回调
  - drain_stdout / drain_stderr 后台读取
"""

import ctypes
import sys
import os
import time
import threading
from ctypes import wintypes

# 加载 pyd
_build_bin = os.path.join(os.path.dirname(__file__), "..", "..", "build", "bin")
_build_bin = os.path.abspath(_build_bin)
if _build_bin not in sys.path:
    sys.path.insert(0, _build_bin)

import win_sandbox_native
from win_sandbox import helpers


# =============================================================================
# 测试框架
# =============================================================================

passed = 0
failed = 0

def check(cond, name):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}")


# =============================================================================
# ctypes 管道创建辅助
# =============================================================================

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreatePipe.argtypes = [
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_void_p, wintypes.DWORD
]
_kernel32.CreatePipe.restype = wintypes.BOOL


def create_pipe():
    """创建匿名管道，返回 (read_handle, write_handle)。"""
    r = ctypes.c_void_p()
    w = ctypes.c_void_p()
    success = _kernel32.CreatePipe(ctypes.byref(r), ctypes.byref(w), None, 0)
    if not success:
        raise OSError(f"CreatePipe failed: err={ctypes.get_last_error()}")
    return r.value, w.value


# =============================================================================
# 测试用例
# =============================================================================

def test_contains_access_denied_keyword():
    """contains_access_denied_keyword 工具函数"""
    print("--- test_contains_access_denied_keyword ---")
    # 英文
    check(win_sandbox_native.contains_access_denied_keyword(b"Access is denied.\r\n"),
          "english 'Access is denied.'")
    check(win_sandbox_native.contains_access_denied_keyword(b"access is denied"),
          "english lowercase")
    check(win_sandbox_native.contains_access_denied_keyword(b"ERROR: Access is denied."),
          "english embedded")
    # 中文 UTF-8
    check(win_sandbox_native.contains_access_denied_keyword("\u62d2\u7edd\u8bbf\u95ee".encode("utf-8")),
          "chinese UTF-8 '拒绝访问'")
    # 中文 GBK
    check(win_sandbox_native.contains_access_denied_keyword("\u62d2\u7edd\u8bbf\u95ee".encode("gbk")),
          "chinese GBK '拒绝访问'")
    # 不命中
    check(not win_sandbox_native.contains_access_denied_keyword(b"hello world"),
          "plain text no match")
    check(not win_sandbox_native.contains_access_denied_keyword(b""),
          "empty bytes")
    check(not win_sandbox_native.contains_access_denied_keyword(b"Permission denied."),
          "unix style no match")


def test_read_write_pipe():
    """read_pipe / write_pipe 读写正确"""
    print("--- test_read_write_pipe ---")
    r_handle, w_handle = create_pipe()
    try:
        # 写入
        data = b"hello world\x00\x01\x02"
        written = helpers.write_pipe(w_handle, data)
        check(written == len(data), f"write_pipe wrote {written} bytes")

        # 读取
        read_data = helpers.read_pipe(r_handle, len(data))
        check(read_data == data, f"read_pipe got correct data: {read_data!r}")
    finally:
        helpers.close_handle(r_handle)
        helpers.close_handle(w_handle)


def test_read_pipe_eof():
    """read_pipe EOF 返回 b''"""
    print("--- test_read_pipe_eof ---")
    r_handle, w_handle = create_pipe()
    try:
        # 关闭写端 → 读端 EOF
        helpers.close_handle(w_handle)
        data = helpers.read_pipe(r_handle, 1024)
        check(data == b"", f"read_pipe EOF returned {data!r}")
    finally:
        helpers.close_handle(r_handle)


def test_close_handle():
    """close_handle 封装"""
    print("--- test_close_handle ---")
    r_handle, w_handle = create_pipe()
    helpers.close_handle(r_handle)
    helpers.close_handle(w_handle)
    check(True, "close_handle no exception")


def test_wall_clock_timer():
    """WallClockTimer 超时触发回调（文档签名 (timeout_ms, on_timeout)）"""
    print("--- test_wall_clock_timer ---")

    fired = []
    timer = helpers.WallClockTimer(100, lambda: fired.append(True))
    timer.start()
    time.sleep(0.3)  # 等待超时
    check(len(fired) == 1, f"on_timeout was called {len(fired)} times")
    check(timer.fired, "timer.fired is True")

    # cancel 测试
    fired2 = []
    timer2 = helpers.WallClockTimer(10000, lambda: fired2.append(True))
    timer2.start()
    timer2.cancel()
    time.sleep(0.2)
    check(len(fired2) == 0, "cancelled timer did not fire")

    # with 上下文管理器
    fired3 = []
    with helpers.WallClockTimer(100, lambda: fired3.append(True)):
        time.sleep(0.3)
    check(len(fired3) == 1, f"with-context timer fired {len(fired3)} times")


def test_stats_poller():
    """StatsPoller 周期回调"""
    print("--- test_stats_poller ---")

    class FakeProc:
        def __init__(self):
            self.call_count = 0
        def query_accounting(self):
            self.call_count += 1
            return {"count": self.call_count}

    proc = FakeProc()
    results = []
    poller = helpers.StatsPoller(proc, interval_ms=50, callback=results.append)
    poller.start()
    time.sleep(0.3)  # 等待几次轮询
    poller.stop()
    check(len(results) >= 2, f"poller called {len(results)} times (expected >= 2)")
    check(all(isinstance(r, dict) for r in results), "all results are dicts")

    # with 上下文管理器
    results2 = []
    with helpers.StatsPoller(proc, interval_ms=50, callback=results2.append):
        time.sleep(0.3)
    check(len(results2) >= 2, f"with-context poller called {len(results2)} times")


def test_drain_stdout():
    """drain_stdout 后台读取"""
    print("--- test_drain_stdout ---")
    r_handle, w_handle = create_pipe()
    try:
        class FakeProc:
            stdout_handle = r_handle

        proc = FakeProc()
        collected = []
        thread = helpers.drain_stdout(proc, collected.append, buffer_size=1024)
        # 写入数据
        helpers.write_pipe(w_handle, b"line1\n")
        helpers.write_pipe(w_handle, b"line2\n")
        time.sleep(0.2)  # 等待读取
        # 关闭写端 → EOF → 线程退出
        helpers.close_handle(w_handle)
        thread.join(timeout=2)
        all_data = b"".join(collected)
        check(all_data == b"line1\nline2\n", f"drained data: {all_data!r}")
        check(not thread.is_alive(), "drain thread exited on EOF")
    finally:
        helpers.close_handle(r_handle)


def test_drain_stderr_with_access_denied():
    """drain_stderr 内置 AccessDenied 扫描"""
    print("--- test_drain_stderr_with_access_denied ---")
    r_handle, w_handle = create_pipe()
    try:
        class FakeProc:
            stderr_handle = r_handle
            on_access_denied = None

        proc = FakeProc()
        ad_events = []
        proc.on_access_denied = lambda data: ad_events.append(data)

        collected = []
        thread = helpers.drain_stderr(proc, collected.append, buffer_size=1024)
        # 写入含 AccessDenied 关键字的数据
        helpers.write_pipe(w_handle, b"Error: Access is denied.\r\n")
        time.sleep(0.3)  # 等待读取 + 扫描
        helpers.close_handle(w_handle)
        thread.join(timeout=2)
        check(len(ad_events) == 1, f"access_denied triggered {len(ad_events)} times")
        check(b"Access is denied" in ad_events[0] if ad_events else False,
              "access_denied data contains keyword")
    finally:
        helpers.close_handle(r_handle)


# =============================================================================
# 主函数
# =============================================================================

def main():
    tests = [
        test_contains_access_denied_keyword,
        test_read_write_pipe,
        test_read_pipe_eof,
        test_close_handle,
        test_wall_clock_timer,
        test_stats_poller,
        test_drain_stdout,
        test_drain_stderr_with_access_denied,
    ]
    for test in tests:
        try:
            test()
        except Exception as e:
            global failed
            failed += 1
            print(f"  FAIL: {test.__name__} raised {type(e).__name__}: {e}")

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
