"""e2e 测试：CI 多实例并行（pybind11 直调形态）。

验证多个沙箱实例同时运行而互不干扰：
  1. 单进程内创建 3 个 SandboxInstance（pybind11 直调形态）
  2. 每个实例执行 `cmd.exe /c echo hello-N`
  3. 每个实例收集 stdout 并验证：
     - stdout 包含 "hello-N"
     - exit_code == 0
     - reason == "normal"
  4. 所有实例必须成功完成，互不干扰

测试模型：
  - 单进程内创建 3 个 SandboxInstance
  - 通过 threading 实现并行启动与输出收集
  - 每个实例独立 drain stdout/stderr + wait + 验证

运行方式（在仓库根目录）：
  python -m pytest tests/e2e/test_scenario_d_ci.py -v
  或
  python tests/e2e/test_scenario_d_ci.py
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402


# 并发实例数
_INSTANCE_COUNT = 3


# =============================================================================
# 辅助
# =============================================================================

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# =============================================================================
# 单实例运行
# =============================================================================

def _run_single_instance(index: int) -> bool:
    """运行单个沙箱实例，返回是否成功。

    创建独立 SandboxInstance，执行 echo hello-{index}，
    收集 stdout 并验证 exit_code / reason。

    Args:
        index: 实例编号（1-based）

    Returns:
        True 表示通过，False 表示失败
    """
    label = f"hello-{index}"
    print(f"\n[Instance {index}] starting ({label})", flush=True)

    sb = make_sandbox(log_level="info")
    try:
        proc = sb.start_process(
            command_line=f"cmd.exe /c echo {label}",
            quota={
                "wall_clock_timeout_ms": 10000,
                "memory_mb": 256,
                "max_processes": 16,
                "no_ui": True,
            },
        )

        stdout_data: list[bytes] = []
        stderr_data: list[bytes] = []
        stdout_thread = helpers.drain_stdout(proc, stdout_data.append)
        stderr_thread = helpers.drain_stderr(proc, stderr_data.append)

        exit_code, reason, usage = proc.wait(timeout_ms=15000)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        proc.close()

        stdout_bytes = b"".join(stdout_data)
        stdout_str = stdout_bytes.decode("utf-8", errors="replace")

        print(f"  [Instance {index}] stdout={stdout_str!r}", flush=True)
        print(f"  [Instance {index}] exit_code={exit_code} reason={reason}", flush=True)

        _assert(label in stdout_str,
                f"stdout should contain {label!r}, got {stdout_str!r}")
        _assert(exit_code == 0, f"exit_code should be 0, got {exit_code}")
        _assert(reason == "normal", f"reason should be 'normal', got {reason!r}")

        print(f"  [Instance {index}] PASS", flush=True)
        return True
    except Exception as e:
        print(f"  [Instance {index}] FAIL: {type(e).__name__}: {e}", flush=True)
        return False
    finally:
        sb.shutdown()


# =============================================================================
# 主入口
# =============================================================================

def run_all() -> int:
    """并行启动 3 个沙箱实例，返回失败数。"""
    results: list[tuple[int, bool]] = []
    threads: list[threading.Thread] = []
    lock = threading.Lock()

    def _runner(idx: int) -> None:
        ok = _run_single_instance(idx)
        with lock:
            results.append((idx, ok))

    print(f"Starting {_INSTANCE_COUNT} sandbox instances in parallel ...",
          flush=True)
    t0 = time.monotonic()

    for i in range(1, _INSTANCE_COUNT + 1):
        t = threading.Thread(target=_runner, args=(i,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    elapsed = time.monotonic() - t0

    # 按实例编号排序输出
    results.sort(key=lambda x: x[0])

    print(f"\n{'=' * 60}", flush=True)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for idx, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  Instance {idx}: {status}", flush=True)
    print(f"\nResult: {passed}/{total} PASS (elapsed={elapsed:.2f}s)", flush=True)

    if passed < total:
        print("Failed instances:", flush=True)
        for idx, ok in results:
            if not ok:
                print(f"  - Instance {idx}", flush=True)

    return total - passed


if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
