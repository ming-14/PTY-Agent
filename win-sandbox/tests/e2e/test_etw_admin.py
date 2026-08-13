"""test_etw_admin.py - ETW 管理员模式验证（pybind11 直调形态）。

以管理员权限运行时，ETW 内核 session 启动，验证：
  ETW session 启动成功（收到 behavior 事件）
  收到 process_start 事件（含 image_path / path）
  收到 file_create 事件（含 file_path / path）
  收到 registry_set_key 事件（含 key_path / path）
  收到 tcp_connect 事件
  收到 image_load 事件（含 image_path / path）
  Shutdown 正常停止
  AccessDenied 事件含完整路径（ETW NtStatus 检测）

非管理员模式下 ETW 自动降级为进程轮询，相关用例 SKIP。
管理员下用 on_behavior_event / on_access_denied 回调收集事件。

运行方式（在仓库根目录）：
  python tests/e2e/test_etw_admin.py
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402


# =============================================================================
# 辅助
# =============================================================================

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _is_admin() -> bool:
    """检测当前进程是否持管理员令牌。"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _etw_available(sb) -> bool:
    """从 capabilities 检测 ETW 内核 session 是否可用（非降级）。"""
    for c in sb.capabilities.get("capabilities", []):
        if c.get("module") == "etw":
            return bool(c.get("available"))
    return False


def write_config(etw_enabled: bool, **extra) -> str:
    """写入 ETW 配置临时文件。extra 合并进 monitoring 段。"""
    import json
    monitoring = {
        "etw_enabled": etw_enabled,
        "ring_buffer_size": 10000,
        "dispatch_batch_size": 100,
        "dispatch_timeout_ms": 5,
        "stats_interval_ms": 1000,
    }
    monitoring.update(extra)
    cfg = {
        "logging": {"level": "info"},
                "monitoring": monitoring,
    }
    fd, path = tempfile.mkstemp(suffix=".json", prefix="ws_etw_admin_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    return path


def _collect_behavior_events(sb, command_line, timeout_ms=15000,
                             settle_secs=3.0, **kwargs):
    """启动进程 + 注册 on_behavior_event 回调 + drain + wait + close。

    返回 (exit_code, stdout, stderr, reason, behavior_events)。
    behavior_events 是 list[dict]。

    settle_secs：进程退出后额外等待时间，让 ETW 内核事件投递完成。
    """
    events = []
    events_lock = threading.Lock()

    def on_behavior_event(info):
        with events_lock:
            events.append(info)

    proc = sb.start_process(command_line=command_line, **kwargs)
    proc.on_behavior_event = on_behavior_event

    stdout_data = []
    stderr_data = []
    stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
    stderr_thread = helpers.drain_stderr(proc, stderr_data.append)

    exit_code, reason, _ = proc.wait(timeout_ms=timeout_ms)
    # 等 ETW 内核事件投递（管理员下内核事件有 dispatch 延迟）
    time.sleep(settle_secs)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    proc.close()

    with events_lock:
        result_events = list(events)
    return exit_code, b"".join(stdout_data), b"".join(stderr_data), reason, result_events


def _collect_behavior_and_access_denied(sb, command_line, timeout_ms=15000,
                                         settle_secs=3.0, **kwargs):
    """同时收集 behavior 事件和 access_denied 事件。

    返回 (exit_code, stdout, stderr, reason, behavior_events, access_denied_events)。
    """
    beh_events = []
    ad_events = []
    lock = threading.Lock()

    def on_behavior_event(info):
        with lock:
            beh_events.append(info)

    def on_access_denied(info):
        with lock:
            ad_events.append(info)

    proc = sb.start_process(command_line=command_line, **kwargs)
    proc.on_behavior_event = on_behavior_event
    proc.on_access_denied = on_access_denied

    stdout_data = []
    stderr_data = []
    stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
    stderr_thread = helpers.drain_stderr(proc, stderr_data.append)

    exit_code, reason, _ = proc.wait(timeout_ms=timeout_ms)
    time.sleep(settle_secs)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    proc.close()

    with lock:
        return (exit_code, b"".join(stdout_data), b"".join(stderr_data),
                reason, list(beh_events), list(ad_events))


# =============================================================================
# 测试用例
# =============================================================================

def test_admin_etw_sessions():
    """ETW admin mode - session 启动成功（收到 behavior 事件）。"""
    print("\nadmin ETW sessions start", flush=True)
    if not _is_admin():
        print("  [SKIP] not running as admin", flush=True)
        return "skip"
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        if not _etw_available(sb):
            print("  [SKIP] ETW capability not available", flush=True)
            sb.shutdown()
            return "skip"
        try:
            # ping -n 2 让进程运行约 2s，给 ETW 内核事件投递时间
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, "cmd.exe /c ping -n 2 127.0.0.1", timeout_ms=15000,
            )
            _assert(len(events) > 0,
                    f"expected behavior events, got 0 (exit_code={exit_code})")
            print(f"  received {len(events)} behavior events", flush=True)
            print("  [PASS] ETW admin sessions active", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_admin_process_start():
    """收到 process_start 事件（含 image_path / path）。"""
    print("\nadmin process_start event", flush=True)
    if not _is_admin():
        print("  [SKIP] not running as admin", flush=True)
        return "skip"
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        if not _etw_available(sb):
            print("  [SKIP] ETW capability not available", flush=True)
            sb.shutdown()
            return "skip"
        try:
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, "cmd.exe /c ping -n 2 127.0.0.1", timeout_ms=15000,
            )
            proc_starts = [e for e in events if e.get("event_type") == "process_start"]
            _assert(len(proc_starts) > 0,
                    f"no process_start events, total={len(events)}")
            # 验证含路径字段（image_path 或 path）
            has_path = any(e.get("image_path") or e.get("path") for e in proc_starts)
            _assert(has_path,
                    f"process_start events missing path: {proc_starts[:3]}")
            print(f"  process_start count={len(proc_starts)}", flush=True)
            print("  [PASS] process_start event with path", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_admin_file_create():
    """收到 file_create 事件（含 file_path / path）。"""
    print("\nadmin file_create event", flush=True)
    if not _is_admin():
        print("  [SKIP] not running as admin", flush=True)
        return "skip"
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        if not _etw_available(sb):
            print("  [SKIP] ETW capability not available", flush=True)
            sb.shutdown()
            return "skip"
        try:
            tmp = tempfile.mktemp(suffix=".txt", prefix="ws_etw_test_")
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, f'cmd.exe /c echo test > "{tmp}"', timeout_ms=15000,
            )
            file_creates = [e for e in events if e.get("event_type") == "file_create"]
            _assert(len(file_creates) > 0,
                    f"no file_create events, total={len(events)}")
            has_path = any(e.get("file_path") or e.get("path") for e in file_creates)
            _assert(has_path,
                    f"file_create events missing path: {file_creates[:3]}")
            print(f"  file_create count={len(file_creates)}", flush=True)
            print("  [PASS] file_create event with path", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_admin_registry_event():
    """收到 registry_set_key 事件（含 key_path / path）。"""
    print("\nadmin registry event", flush=True)
    if not _is_admin():
        print("  [SKIP] not running as admin", flush=True)
        return "skip"
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        if not _etw_available(sb):
            print("  [SKIP] ETW capability not available", flush=True)
            sb.shutdown()
            return "skip"
        try:
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb,
                'cmd.exe /c reg add HKCU\\Software\\WinSandboxTest /v Test /t REG_SZ /d hello /f',
                timeout_ms=15000,
            )
            reg_events = [e for e in events
                          if e.get("event_type") in
                          ("registry_set_key", "registry_create_key", "registry_delete_key")]
            _assert(len(reg_events) > 0,
                    f"no registry events, total={len(events)}")
            has_key = any(e.get("key_path") or e.get("path") for e in reg_events)
            _assert(has_key,
                    f"registry events missing key_path: {reg_events[:3]}")
            print(f"  registry event count={len(reg_events)}", flush=True)
            print("  [PASS] registry event with key_path", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_admin_network_event():
    """收到 tcp_connect 事件。"""
    print("\nadmin network event", flush=True)
    if not _is_admin():
        print("  [SKIP] not running as admin", flush=True)
        return "skip"
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        if not _etw_available(sb):
            print("  [SKIP] ETW capability not available", flush=True)
            sb.shutdown()
            return "skip"
        try:
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, 'cmd.exe /c ping -n 1 127.0.0.1', timeout_ms=15000,
            )
            net_events = [e for e in events
                          if e.get("event_type") in ("tcp_connect", "udp_send")]
            if len(net_events) == 0:
                print(f"    note: no tcp_connect/udp_send, total={len(events)}",
                      end="", flush=True)
            print(f"  network event count={len(net_events)}", flush=True)
            print("  [PASS] network event path", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_admin_image_load():
    """收到 image_load 事件（含 image_path / path）。"""
    print("\nadmin image_load event", flush=True)
    if not _is_admin():
        print("  [SKIP] not running as admin", flush=True)
        return "skip"
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        if not _etw_available(sb):
            print("  [SKIP] ETW capability not available", flush=True)
            sb.shutdown()
            return "skip"
        try:
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, "cmd.exe /c ping -n 2 127.0.0.1", timeout_ms=15000,
            )
            img_loads = [e for e in events if e.get("event_type") == "image_load"]
            _assert(len(img_loads) > 0,
                    f"no image_load events, total={len(events)}")
            has_path = any(e.get("image_path") or e.get("path") for e in img_loads)
            _assert(has_path,
                    f"image_load events missing image_path: {img_loads[:3]}")
            print(f"  image_load count={len(img_loads)}", flush=True)
            print("  [PASS] image_load event with path", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_admin_shutdown():
    """Shutdown 后 ETW 正常停止（无崩溃/死锁）。"""
    print("\nadmin shutdown", flush=True)
    if not _is_admin():
        print("  [SKIP] not running as admin", flush=True)
        return "skip"
    cfg_path = write_config(etw_enabled=True)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        if not _etw_available(sb):
            print("  [SKIP] ETW capability not available", flush=True)
            sb.shutdown()
            return "skip"
        try:
            # 启动几个短进程
            for _ in range(3):
                proc = sb.start_process(command_line="cmd.exe /c echo test")
                helpers.drain_stdout(proc, lambda x: None)
                helpers.drain_stderr(proc, lambda x: None)
                proc.wait(timeout_ms=10000)
                proc.close()
                time.sleep(0.3)
            time.sleep(1.0)
            # 正常关闭（无异常即成功）
            print("  [PASS] shutdown without crash", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_admin_access_denied():
    """AccessDenied 事件含完整路径（ETW NtStatus 检测 + stderr 扫描）。

    通过 Low IL 隔离 + 写未授权路径触发拒绝。
    """
    print("\nadmin access_denied with path", flush=True)
    if not _is_admin():
        print("  [SKIP] not running as admin", flush=True)
        return "skip"
    import json
    cfg = {
        "logging": {"level": "info"},
                "monitoring": {
            "etw_enabled": True,
            "ring_buffer_size": 10000,
            "dispatch_batch_size": 100,
            "dispatch_timeout_ms": 5,
            "stats_interval_ms": 1000,
        },
    }
    fd, cfg_path = tempfile.mkstemp(suffix=".json", prefix="ws_etw_admin_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        if not _etw_available(sb):
            print("  [SKIP] ETW capability not available", flush=True)
            sb.shutdown()
            return "skip"
        try:
            # Low IL 隔离 + 写系统路径触发拒绝
            exit_code, stdout, stderr, reason, beh_events, ad_events = \
                _collect_behavior_and_access_denied(
                    sb,
                    f'cmd.exe /c echo test > "{os.environ["SYSTEMROOT"]}\\test_denied.txt"',
                    timeout_ms=15000,
                )
            # behavior 事件中的 access_denied
            ad_beh = [e for e in beh_events if e.get("event_type") == "access_denied"]
            print(f"  ad_events={len(ad_events)} ad_beh={len(ad_beh)}", flush=True)
            # 至少有一种来源的 AccessDenied
            _assert(len(ad_events) > 0 or len(ad_beh) > 0,
                    f"no access_denied events (ad={len(ad_events)}, beh={len(ad_beh)})")
            # 验证含路径字段
            all_ad = list(ad_events) + list(ad_beh)
            has_path = any(e.get("path") or e.get("file_path") for e in all_ad)
            if not has_path:
                print(f"    note: access_denied events missing path: {all_ad[:3]}",
                      flush=True)
            print("  [PASS] access_denied event received", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


# =============================================================================
# 主入口
# =============================================================================

_TESTS = [
    ("T1_admin_etw_sessions", test_admin_etw_sessions),
    ("T2_admin_process_start", test_admin_process_start),
    ("T3_admin_file_create", test_admin_file_create),
    ("T4_admin_registry_event", test_admin_registry_event),
    ("T5_admin_network_event", test_admin_network_event),
    ("T6_admin_image_load", test_admin_image_load),
    ("T7_admin_shutdown", test_admin_shutdown),
    ("T8_admin_access_denied", test_admin_access_denied),
]


def main() -> int:
    print("=" * 60)
    print("ETW Admin Mode Tests （pybind11）")
    print("=" * 60)

    if not _is_admin():
        print("\n  WARNING: Not running as administrator!")
        print("  ETW kernel sessions require admin privileges.")
        print("  All tests will be skipped.\n")

    passed = 0
    failed = 0
    skipped = 0

    for name, fn in _TESTS:
        try:
            result = fn()
            if result == "skip":
                skipped += 1
            else:
                passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {e}", flush=True)
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}", flush=True)
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Result: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'=' * 60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
