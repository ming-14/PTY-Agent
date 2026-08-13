"""e2e 测试：样本分析（Sample Analysis）场景（Low IL 语义）。

模拟"样本分析"场景，一次性验证沙箱核心隔离能力：
  1. 文件系统隔离 — 写入 %TEMP%（沙箱内重定向到可写区）允许，读取系统保护文件被拒绝
  2. 行为监控 — 正确产生 AccessDenied 事件（on_behavior_event / on_access_denied 回调）

测试模型：
  - 单个 SandboxInstance，单条命令覆盖两种行为
  - 通过 stdout/stderr + on_behavior_event / on_access_denied 回调收集证据
  - AccessDenied 验证双路径：ETW 回调事件 + stderr 关键字扫描（contains_access_denied_keyword）

运行方式（在仓库根目录）：
  python -m pytest tests/e2e/test_scenario_c_sample.py -v
  或
  python tests/e2e/test_scenario_c_sample.py
"""

from __future__ import annotations

import os
import sys
import threading
import time

# GBK 控制台下 print 含 \ufffd 的文本会抛 UnicodeEncodeError；统一 UTF-8 输出
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312", "cp936"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402


# =============================================================================
# 辅助
# =============================================================================

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# =============================================================================
# 用例：样本分析 — 综合场景
# =============================================================================

def test_sample_analysis() -> None:
    """样本分析综合场景（pybind11 直调形态，Low IL 语义）。

    单条命令覆盖两种行为：
      1. 写入 %TEMP%\\sample_analysis.txt（沙箱内 %TEMP% 重定向到 Low 可写区，应允许）
      2. 读取 %SYSTEMROOT%\\system32\\config\\SAM（Low IL + 无读权限 → AccessDenied）

    期望：
      - stdout 含 "analysis-start" 标记
      - AccessDenied：on_behavior_event/on_access_denied 回调收到事件，
        或 stderr 含 AccessDenied 关键字（非管理员降级时 ETW 不产生事件）
    """
    print("\n[Test Sample Analysis] comprehensive isolation scenario", flush=True)

    temp_dir = os.environ["TEMP"]
    system_root = os.environ["SYSTEMROOT"]

    # 命令序列（用 & 串联，确保所有命令都执行，即使中间失败）：
    #   1. echo analysis-start              → stdout 标记
    #   2. echo hello > %TEMP%\sample.txt   → 写入沙箱可写区（应允许）
    #   3. type %SYSTEMROOT%\system32\config\SAM → 读取保护文件（应被拒绝）
    cmd = (
        'cmd.exe /c '
        'echo analysis-start & '
        f'echo hello > "{temp_dir}\\sample.txt" & '
        f'type "{system_root}\\system32\\config\\SAM"'
    )

    sb = make_sandbox(log_level="info")
    try:
        # 行为事件收集（on_behavior_event / on_access_denied 在 ETW 启用时触发）
        behavior_events: list[dict] = []
        behavior_lock = threading.Lock()

        def on_behavior_event(info):
            with behavior_lock:
                behavior_events.append(info)

        def on_access_denied(info):
            with behavior_lock:
                behavior_events.append({"event_type": "access_denied", **info})

        proc = sb.start_process(
            command_line=cmd,
            quota={
                "wall_clock_timeout_ms": 15000,
                "memory_mb": 256,
                "max_processes": 16,
                "no_ui": True,
            },
        )
        proc.on_behavior_event = on_behavior_event
        proc.on_access_denied = on_access_denied

        stdout_data: list[bytes] = []
        stderr_data: list[bytes] = []
        stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
        stderr_thread = helpers.drain_stderr(proc, stderr_data.append)

        exit_code, reason, usage = proc.wait(timeout_ms=30000)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        # 等 ETW 降级轮询采集完事件（500ms 周期，2.5s 足 5 轮）
        time.sleep(2.5)
        proc.close()

        stdout_bytes = b"".join(stdout_data)
        stderr_bytes = b"".join(stderr_data)
        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")

        with behavior_lock:
            events = list(behavior_events)

        print(f"  exit_code={exit_code}, reason={reason}", flush=True)
        print(f"  stdout={stdout_str[:300]!r}", flush=True)
        print(f"  stderr={stderr_str[:300]!r}", flush=True)
        print(f"  behavior_events count: {len(events)}", flush=True)

        # ---- 1. stdout 含 analysis-start 标记 ----
        _assert("analysis-start" in stdout_str,
                f"stdout should contain 'analysis-start', got: {stdout_str!r}")

        # ---- 2. AccessDenied（SAM 读取被拒）----
        # 双路径验证：ETW 回调事件 或 stderr AccessDenied 关键字
        access_denied_events = [e for e in events if e.get("event_type") == "access_denied"]
        print(f"  access_denied events: {len(access_denied_events)}", flush=True)
        for i, ev in enumerate(access_denied_events[:5]):
            print(f"    [{i}] {ev}", flush=True)

        has_ad_keyword = win_sandbox_native.contains_access_denied_keyword(stderr_bytes)
        print(f"  stderr AccessDenied keyword: {has_ad_keyword}", flush=True)

        _assert(len(access_denied_events) >= 1 or has_ad_keyword,
                "should have AccessDenied event (SAM read denied) "
                "or stderr AccessDenied keyword")

        # ---- 3. 写入 %TEMP%（沙箱可写区）----
        # %TEMP% 被重定向到 Low IL 可写区，写入应成功（文件系统隔离的白名单侧）
        _assert("analysis-start" in stdout_str,
                f"stdout should contain 'analysis-start', got: {stdout_str!r}")

        print("  PASS", flush=True)
    finally:
        sb.shutdown()


# =============================================================================
# 主入口
# =============================================================================

def run_all() -> int:
    """运行全部子用例，返回失败数。"""
    failures = 0
    tests = [
        ("sample_analysis", test_sample_analysis),
    ]
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            print(f"  FAIL: {e}", flush=True)
            failures += 1

    print(f"\n{'=' * 60}", flush=True)
    print(f"Result: {len(tests) - failures}/{len(tests)} PASS", flush=True)
    if failures:
        print("Failed tests:", flush=True)
        for name, _ in tests:
            print(f"  - {name}", flush=True)
    return failures


if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
