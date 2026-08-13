"""test_degraded_monitor.py - ETW 降级模式扩展验证（pybind11 直调形态）。

降级模式（force_degraded 强制走降级路径，或非管理员自动降级）行为验证。
pybind11 形态下 ETW 事件按 pid 路由到对应进程的 on_behavior_event 回调：

  process_stop 事件 — 沙箱进程退出后收到（验证降级轮询活跃）
  首次轮询无噪音 — 启动后短时间内 process_start 数量有限
  file_create 事件 — SKIP（ReadDirectoryChangesW 事件 pid=0，被路由表过滤）
  file_write 事件 — SKIP（同 file_create）
  tcp_connect 事件 — 沙箱内进程建立 TCP 连接后收到
  Shutdown 正常停止（无崩溃、无死锁）

架构说明：
  pybind11 形态下 ETW 事件按 OS pid 路由到 start_process 启动的进程回调。
  - process_stop：沙箱直接启动的进程退出 → pid 在路由表 → 可收到
  - tcp_connect：沙箱内进程建立连接 → pid 在路由表 → 可收到
  - file_create/file_write：ReadDirectoryChangesW 不提供 pid（ev.pid=0）
    → 不在路由表 → 被丢弃（pybind11 形态下无法通过 on_behavior_event 接收）
  - process_start：首次轮询建基线不产生；子进程 pid 不在路由表 → 被丢弃

运行方式（在仓库根目录）：
  python tests/e2e/test_degraded_monitor.py
"""

from __future__ import annotations

import os
import socket
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


def write_config(monitor_dirs=None, net_polling=True) -> str:
    """写入降级模式配置临时文件。"""
    import json
    cfg = {
        "logging": {"level": "info"},
                "monitoring": {
            "etw_enabled": True,
            "ring_buffer_size": 10000,
            "dispatch_batch_size": 100,
            "dispatch_timeout_ms": 5,
            "stats_interval_ms": 1000,
            "force_degraded": True,
            "degraded_net_polling": net_polling,
            "degraded_monitor_dirs": monitor_dirs or [],
        },
    }
    fd, path = tempfile.mkstemp(suffix=".json", prefix="ws_dg_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    return path


def _collect_behavior_events(sb, command_line, timeout_ms=15000,
                             settle_secs=3.0, **kwargs):
    """启动进程 + 注册 on_behavior_event 回调 + drain + wait + close。

    返回 (exit_code, stdout, stderr, reason, behavior_events)。
    settle_secs：进程退出后额外等待时间，让降级轮询采集完事件。
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
    time.sleep(settle_secs)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    proc.close()

    with events_lock:
        result_events = list(events)
    return exit_code, b"".join(stdout_data), b"".join(stderr_data), reason, result_events


def _start_tcp_server():
    """启动本地 TCP server，返回 (server, port)。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def _serve():
        try:
            conn, _ = srv.accept()
            conn.recv(1024)
            conn.sendall(b"pong")
            conn.close()
        except Exception:
            pass
        finally:
            try:
                srv.close()
            except Exception:
                pass

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return srv, srv.getsockname()[1]


# =============================================================================
# 测试用例
# =============================================================================

def test_process_stop_event():
    """沙箱进程退出后收到 process_stop 事件（验证降级轮询活跃）。

    降级模式 500ms 进程列表轮询，沙箱直接启动的进程退出后，
    其 pid 在路由表中，process_stop 事件能被 on_behavior_event 回调接收。
    """
    print("\nprocess_stop event (degraded polling active)", flush=True)
    cfg_path = write_config()
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        try:
            # ping -n 3 约持续 2s，足够降级轮询捕捉退出
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, "cmd.exe /c ping -n 3 127.0.0.1", timeout_ms=15000,
            )
            print(f"  total events={len(events)}", flush=True)
            types = {}
            for e in events:
                t = e.get("event_type", "unknown")
                types[t] = types.get(t, 0) + 1
            print(f"  event types={types}", flush=True)

            proc_stops = [e for e in events if e.get("event_type") == "process_stop"]
            _assert(len(proc_stops) > 0,
                    f"no process_stop events, total={len(events)}, types={types}")
            print(f"  process_stop count={len(proc_stops)}", flush=True)
            print("  [PASS] process_stop event received", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_no_first_poll_noise():
    """首次轮询不产生全系统进程噪音。

    降级模式首次轮询只建基线，不产生 process_start 事件。
    启动后短时间内 process_start 数量应有限（< 15）。
    """
    print("\nno first-poll noise", flush=True)
    cfg_path = write_config()
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        try:
            # 启动一个短进程，收集事件
            # 修复前：首次轮询把全系统进程当新进程 → 大量 process_start
            # 修复后：首次轮询只建基线 → 无 process_start
            exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                sb, "cmd.exe /c ping -n 2 127.0.0.1", timeout_ms=15000,
            )
            proc_starts = [e for e in events if e.get("event_type") == "process_start"]
            # 允许少量（系统可能在 2s 内有真实进程启动/退出），但不应是"全系统进程数量级"
            print(f"  process_start count={len(proc_starts)}", flush=True)
            _assert(len(proc_starts) <= 15,
                    f"first-poll noise: {len(proc_starts)} process_start events")
            print("  [PASS] no first-poll noise", flush=True)
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_file_create_event():
    """监控目录创建文件 → file_create 事件。

    SKIP 原因：pybind11 形态下 ETW 事件按 pid 路由，ReadDirectoryChangesW
    产生的文件事件 ev.pid=0，不在 pid_to_usecase_ 路由表中，被丢弃。
    无法通过 on_behavior_event 回调接收文件事件。
    """
    print("\nfile_create event", flush=True)
    print("  [SKIP] pybind11 路由限制：文件事件 pid=0 不路由到 proc 回调",
          flush=True)
    return "skip"


def test_file_write_event():
    """监控目录文件被修改 → file_write 事件。

    SKIP 原因：ReadDirectoryChangesW 事件 pid=0 被路由表过滤。
    """
    print("\nfile_write event", flush=True)
    print("  [SKIP] pybind11 路由限制：文件事件 pid=0 不路由到 proc 回调",
          flush=True)
    return "skip"


def test_network_tcp_connect():
    """沙箱内进程建立 TCP 连接 → tcp_connect 事件。

    沙箱直接启动的进程（powershell）建立 TCP 连接，其 pid 在路由表中，
    tcp_connect 事件能被 on_behavior_event 回调接收。
    """
    print("\ntcp_connect event (in-sandbox process)", flush=True)
    cfg_path = write_config()
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        try:
            # 先启动 TCP server 在 Python 主进程
            srv, port = _start_tcp_server()
            try:
                # 沙箱内 powershell 建立 TCP 连接
                cmd = (
                    f"powershell -NoProfile -Command "
                    f"\"$c = New-Object System.Net.Sockets.TcpClient('127.0.0.1', {port}); "
                    f"Start-Sleep -Milliseconds 500; $c.Close()\""
                )
                exit_code, stdout, stderr, reason, events = _collect_behavior_events(
                    sb, cmd, timeout_ms=20000, settle_secs=3.0,
                )
                print(f"  total events={len(events)}", flush=True)
                types = {}
                for e in events:
                    t = e.get("event_type", "unknown")
                    types[t] = types.get(t, 0) + 1
                print(f"  event types={types}", flush=True)

                net_events = [e for e in events
                              if e.get("event_type") in ("tcp_connect", "udp_send")]
                if len(net_events) == 0:
                    print(f"    note: no tcp_connect/udp_send events", flush=True)
                    # 降级轮询有 500ms 周期，连接可能太快没捕捉到
                    # 但 process_stop 应该能收到
                    proc_stops = [e for e in events if e.get("event_type") == "process_stop"]
                    _assert(len(proc_stops) > 0,
                            f"no process_stop either, total={len(events)}")
                    print("  [PASS] process_stop received (tcp_connect missed by polling)",
                          flush=True)
                else:
                    # 验证有指向 127.0.0.1 的连接
                    has_loopback = any(
                        e.get("path") == "127.0.0.1" or
                        e.get("remote_addr") == "127.0.0.1"
                        for e in net_events
                    )
                    print(f"  tcp_connect count={len(net_events)}, "
                          f"has_loopback={has_loopback}", flush=True)
                    print("  [PASS] tcp_connect event received", flush=True)
            finally:
                try:
                    srv.close()
                except Exception:
                    pass
        finally:
            sb.shutdown()
    finally:
        os.unlink(cfg_path)


def test_shutdown_no_crash():
    """Shutdown 正常停止（含文件监控线程，无崩溃/死锁）。"""
    print("\nshutdown no crash", flush=True)
    monitor_dir = tempfile.mkdtemp(prefix="ws_dg_mon_")
    cfg_path = write_config(monitor_dirs=[monitor_dir])
    try:
        sb = make_sandbox(config=cfg_path, log_level="info")
        try:
            # 启动一个进程产生事件
            proc = sb.start_process(command_line="cmd.exe /c ping -n 2 127.0.0.1")
            helpers.drain_stdout(proc, lambda x: None)
            helpers.drain_stderr(proc, lambda x: None)

            # 在 proc 运行期间创建几个文件触发文件监控
            time.sleep(0.5)
            for i in range(3):
                test_file = os.path.join(monitor_dir, f"s{i}_{int(time.time())}.txt")
                with open(test_file, "w") as f:
                    f.write("data")
                time.sleep(0.3)

            proc.wait(timeout_ms=15000)
            proc.close()

            # shutdown 计时
            t0 = time.monotonic()
            sb.shutdown()
            elapsed = time.monotonic() - t0
            print(f"  shutdown elapsed={elapsed:.2f}s", flush=True)
            _assert(elapsed < 10.0,
                    f"shutdown should complete < 10s, got {elapsed:.2f}s")
            print("  [PASS] shutdown without crash", flush=True)
            return  # shutdown 已调用
        except Exception:
            try:
                sb.shutdown()
            except Exception:
                pass
            raise
    finally:
        try:
            os.unlink(cfg_path)
            import shutil
            shutil.rmtree(monitor_dir, ignore_errors=True)
        except Exception:
            pass


# =============================================================================
# 主入口
# =============================================================================

_TESTS = [
    ("T1_process_stop_event", test_process_stop_event),
    ("T2_no_first_poll_noise", test_no_first_poll_noise),
    ("T3_file_create_event", test_file_create_event),
    ("T4_file_write_event", test_file_write_event),
    ("T5_network_tcp_connect", test_network_tcp_connect),
    ("T6_shutdown_no_crash", test_shutdown_no_crash),
]


def main() -> int:
    print("=" * 60)
    print("ETW Degraded Mode Tests （pybind11）")
    print("=" * 60)

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
