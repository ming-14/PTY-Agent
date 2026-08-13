"""test_native_etw.py - ETW 行为监控回调测试

测试：
  - ETW 启用后 SandboxInstance 可创建进程
  - on_behavior_event 回调触发（降级模式下进程事件）
  - on_access_denied 回调（ETW 或 stderr 扫描）
  - ETW 配置通过 config 启用

注意：
  - 管理员模式：ETW 内核 session（文件/注册表/网络事件）
  - 普通用户模式：降级轮询（进程 start/stop 事件）
  - 测试兼容两种模式
"""

import sys
import os
import time
import threading

# 加载 pyd
_build_bin = os.path.join(os.path.dirname(__file__), "..", "..", "build", "bin")
_build_bin = os.path.abspath(_build_bin)
if _build_bin not in sys.path:
    sys.path.insert(0, _build_bin)

import win_sandbox_native


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
# 测试用例
# =============================================================================

def test_etw_enabled_basic():
    """ETW 启用后基本功能正常（进程启动+退出）"""
    print("--- test_etw_enabled_basic ---")
    config = {
        "monitoring": {
            "etw_enabled": True,
        }
    }
    sb = win_sandbox_native.SandboxInstance(config)
    try:
        proc = sb.start_process(command_line="cmd.exe /c echo hello")
        check(proc is not None, "start_process with ETW enabled")

        # 等待退出
        exit_code, exit_reason, usage = proc.wait(timeout_ms=10000)
        check(exit_code == 0, f"exit_code={exit_code}")
        check(exit_reason in ("normal", "normal_exit"), f"exit_reason={exit_reason}")
        proc.close()
    finally:
        sb.shutdown()


def test_behavior_event_callback():
    """on_behavior_event 回调触发（降级模式下进程事件）

    用 ping -n 2 让进程运行约 2 秒，给 ETW 降级轮询足够时间检测。
    """
    print("--- test_behavior_event_callback ---")
    config = {
        "monitoring": {
            "etw_enabled": True,
        }
    }
    sb = win_sandbox_native.SandboxInstance(config)
    try:
        proc = sb.start_process(command_line="cmd.exe /c ping -n 2 127.0.0.1")

        events = []
        events_lock = threading.Lock()
        event_received = threading.Event()

        def on_behavior_event(info):
            with events_lock:
                events.append(info)
            event_received.set()

        proc.on_behavior_event = on_behavior_event

        # 等待进程退出
        proc.wait(timeout_ms=10000)

        # 等待 ETW 事件（降级模式有轮询延迟，最多等 3 秒）
        event_received.wait(timeout=3.0)

        with events_lock:
            check(len(events) > 0, f"received {len(events)} behavior events")
            if events:
                check("event_type" in events[0], f"event has event_type: {events[0]}")
                check("pid" in events[0], "event has pid")
                check("source" in events[0], "event has source")
                print(f"    first event: {events[0]}")

        proc.close()
    finally:
        sb.shutdown()


def test_access_denied_callback_stderr():
    """on_access_denied 回调（stderr 关键字扫描，通过 helpers.drain_stderr）

    注意：Low IL 下 cmd.exe 访问受保护路径的 stderr 输出格式不确定，
    stderr 扫描的单元测试在 test_helpers.py 中已覆盖（test_drain_stderr_with_access_denied）。
    此测试验证 ETW 启用 + Low IL 隔离下进程能正常退出。
    """
    print("--- test_access_denied_callback_stderr ---")
    sb = win_sandbox_native.SandboxInstance()
    try:
        proc = sb.start_process(
            command_line=f'cmd.exe /c type {os.environ["SYSTEMROOT"]}\\System32\\config\\SAM',
        )
        check(proc is not None, "start_process with Low IL isolation")

        # 等待进程退出（Low IL 隔离下应非 0 退出码）
        exit_code, exit_reason, _ = proc.wait(timeout_ms=10000)
        check(exit_code != 0, f"exit_code={exit_code} (expected non-zero, access denied)")
        proc.close()
    finally:
        sb.shutdown()


def test_etw_disabled_no_crash():
    """ETW 未启用时不崩溃（默认配置）"""
    print("--- test_etw_disabled_no_crash ---")
    sb = win_sandbox_native.SandboxInstance()
    try:
        proc = sb.start_process(command_line="cmd.exe /c echo hello")
        check(proc is not None, "start_process without ETW")

        # 设置回调（不应触发，但不应该崩溃）
        proc.on_behavior_event = lambda info: None
        proc.on_access_denied = lambda info: None

        exit_code, _, _ = proc.wait(timeout_ms=10000)
        check(exit_code == 0, f"exit_code={exit_code}")
        proc.close()
    finally:
        sb.shutdown()


def test_etw_config_passthrough():
    """ETW 配置通过 config 传入（monitoring 段）"""
    print("--- test_etw_config_passthrough ---")
    config = {
        "monitoring": {
            "etw_enabled": True,
        }
    }
    sb = win_sandbox_native.SandboxInstance(config)
    try:
        proc = sb.start_process(command_line="cmd.exe /c echo hello")
        check(proc is not None, "start_process with ETW config")
        proc.wait(timeout_ms=10000)
        proc.close()
    finally:
        sb.shutdown()


# =============================================================================
# 主函数
# =============================================================================

def main():
    tests = [
        test_etw_enabled_basic,
        test_behavior_event_callback,
        test_access_denied_callback_stderr,
        test_etw_disabled_no_crash,
        test_etw_config_passthrough,
    ]
    for test in tests:
        try:
            test()
        except Exception as e:
            global failed
            failed += 1
            print(f"  FAIL: {test.__name__} raised {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
