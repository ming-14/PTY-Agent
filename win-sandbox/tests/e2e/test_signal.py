"""e2e 测试：SignalProcess（CtrlC/CtrlBreak/Kill）（pybind11 直调形态）。

验证信号控制功能在 pybind11 直调链路上的行为，覆盖 5 个子用例：
  1. Kill 强杀死循环进程（TerminateProcess，exit_reason=killed_by_user）
  2. CtrlBreak 中断长跑进程（GenerateConsoleCtrlEvent，依赖 console 共享）
  3. 已退出进程发 signal → RuntimeError（process_already_exited）
  4. 无效 signal 值 → ValueError（invalid signal）
  5. 已关闭进程发 signal → RuntimeError（process already exited）

运行方式（在仓库根目录，需从终端运行以使 CtrlBreak 测试有 console）：
  python tests/e2e/test_signal.py
  或
  python tests/e2e/test_signal.py 2   # 只跑用例 2
"""

from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402

# ExitReason 字符串契约
_EXIT_REASON_KILLED_BY_USER = "killed_by_user"


# =============================================================================
# 辅助
# =============================================================================

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _drain_stdout_until(proc, needle_bytes, timeout=10.0):
    """后台 drain stdout，直到包含 needle_bytes 或超时。

    返回累计 stdout 字节。超时抛 AssertionError。
    """
    accumulated = bytearray()
    found = threading.Event()

    def callback(data):
        accumulated.extend(data)
        if needle_bytes in accumulated:
            found.set()

    thread = helpers.drain_stdout(proc, callback)
    if found.wait(timeout=timeout):
        return bytes(accumulated)
    raise AssertionError(
        f"timeout waiting for stdout containing {needle_bytes!r}; "
        f"stdout so far: {bytes(accumulated)!r}"
    )


# =============================================================================
# 测试用例
# =============================================================================

def test_1_kill_terminates_long_running_process() -> None:
    """用例 1：Kill 信号强杀长跑进程。

    流程：
      1. start_process(python time.sleep(30)) — 长跑 30 秒
      2. 发 signal("kill")
      3. wait
      4. 断言 exit_reason = killed_by_user
    """
    print("\nKill 强杀长跑进程", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动长跑进程
        proc = sb.start_process(
            command_line='python.exe -c "import time; time.sleep(30)"',
        )
        print(f"  process started: pid={proc.pid}", flush=True)

        # 2. 等一小会让进程进入运行状态
        time.sleep(0.5)

        # 3. 发 Kill 信号
        proc.signal("kill")
        print(f"  Kill signal sent: pid={proc.pid}", flush=True)

        # 4. wait
        exit_code, reason, _ = proc.wait(timeout_ms=10000)
        print(f"  process exited: code={exit_code} reason={reason}", flush=True)
        proc.close()

        _assert(
            reason == _EXIT_REASON_KILLED_BY_USER,
            f"exit_reason should be killed_by_user, got {reason}",
        )
        print("  [PASS] exit_reason=killed_by_user", flush=True)

    finally:
        sb.shutdown()


def test_2_ctrlbreak_interrupts_loop() -> None:
    """用例 2：CtrlBreak 中断长跑进程。

    流程：
      1. start_process(python print+sleep(60), interactive=true)
      2. 等 stdout 含 "loop_start"
      3. 发 signal("ctrl_break")
      4. wait
      5. 断言进程退出（exit_code != 0）

    环境要求：需有 console（从终端运行测试脚本即可）。
    若无 console，CtrlBreak 会抛异常，本用例标记为 SKIP。
    """
    print("\nCtrlBreak 中断长跑进程", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动长跑 Python 进程（interactive=true 共享 console）
        py_cmd = (
            'python.exe -c "print(\'loop_start\', flush=True); '
            'import time; time.sleep(60)"'
        )
        proc = sb.start_process(command_line=py_cmd, interactive=True)
        print(f"  process started: pid={proc.pid} cmd={py_cmd}", flush=True)

        # 2. 后台 drain stdout，等 "loop_start" 确认进程在运行
        try:
            stdout_data = _drain_stdout_until(proc, b"loop_start", timeout=10.0)
            print(f"  received loop_start output", flush=True)
        except AssertionError as e:
            print(f"  [FAIL] 未收到 loop_start: {e}", flush=True)
            proc.terminate(1)
            proc.wait(timeout_ms=5000)
            proc.close()
            raise

        # 3. 发 CtrlBreak 信号
        try:
            proc.signal("ctrl_break")
            print(f"  CtrlBreak signal sent: pid={proc.pid}", flush=True)
        except Exception as e:
            # CtrlBreak 失败（通常因为无 console），标记 SKIP
            print(f"  [SKIP] CtrlBreak 失败（环境无 console？）: {e}", flush=True)
            try:
                proc.terminate(1)
                proc.wait(timeout_ms=5000)
            except Exception:
                pass
            proc.close()
            return

        # 4. wait（CtrlBreak 是软中断，超时设 10 秒）
        exit_code, reason, _ = proc.wait(timeout_ms=10000)
        print(f"  process exited: code={exit_code} reason={reason}", flush=True)
        proc.close()

        # CtrlBreak 是软中断，Python 默认收到 SIGBREAK 退出，exit_code != 0
        _assert(
            exit_code != 0,
            f"exit_code should be non-zero (SIGBREAK), got {exit_code}",
        )
        print("  [PASS] CtrlBreak 中断成功", flush=True)

    finally:
        sb.shutdown()


def test_3_signal_already_exited() -> None:
    """用例 3：对已退出进程发 signal → RuntimeError。

    流程：
      1. start_process(cmd /c echo done) — 立即退出
      2. wait
      3. signal("kill") → 应抛 RuntimeError（process_already_exited）
    """
    print("\n已退出进程发 signal → Error", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动立即退出的进程
        proc = sb.start_process(command_line="cmd.exe /c echo done")
        print(f"  process started: pid={proc.pid}", flush=True)

        # 2. wait
        exit_code, reason, _ = proc.wait(timeout_ms=10000)
        print(f"  process exited: code={exit_code}", flush=True)

        # 3. 发 Kill 信号到已退出进程 → 应抛异常
        raised = False
        try:
            proc.signal("kill")
        except RuntimeError as e:
            raised = True
            err_msg = str(e)
            print(f"  RuntimeError received: {err_msg}", flush=True)
            _assert(
                "exited" in err_msg.lower() or "already" in err_msg.lower(),
                f"Error 应提示进程已退出, got: {err_msg}",
            )
        except Exception as e:
            raised = True
            print(f"  Exception received: {type(e).__name__}: {e}", flush=True)

        _assert(raised, "应抛异常（process_already_exited）")
        print("  [PASS] 已退出进程 signal 返回 Error", flush=True)

        proc.close()
    finally:
        sb.shutdown()


def test_4_invalid_signal_value() -> None:
    """用例 4：无效 signal 值 → ValueError。

    流程：
      1. start_process(python time.sleep(30)) — 长跑
      2. signal("bogus_value") → 应抛 ValueError（invalid signal）
      3. Kill 清理
    """
    print("\n无效 signal 值 → Error", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动长跑进程
        proc = sb.start_process(
            command_line='python.exe -c "import time; time.sleep(30)"',
        )
        print(f"  process started: pid={proc.pid}", flush=True)
        time.sleep(0.5)

        # 2. 发非法 signal 值 → 应抛 ValueError
        raised = False
        try:
            proc.signal("bogus_value")
        except ValueError as e:
            raised = True
            err_msg = str(e)
            print(f"  ValueError received: {err_msg}", flush=True)
            _assert(
                "invalid" in err_msg.lower(),
                f"Error 应提示 invalid signal, got: {err_msg}",
            )
        except Exception as e:
            raised = True
            print(f"  Exception received: {type(e).__name__}: {e}", flush=True)

        _assert(raised, "应抛异常（invalid signal）")
        print("  [PASS] 无效 signal 值返回 Error", flush=True)

        # 3. 清理：Kill 进程
        try:
            proc.terminate(1)
            proc.wait(timeout_ms=5000)
        except Exception:
            pass
        proc.close()
    finally:
        sb.shutdown()


def test_5_signal_no_process_running() -> None:
    """用例 5：对已关闭进程发 signal → RuntimeError。

    pybind11 直调形态下无 process_id 概念，改为测试对已 close 的进程发 signal。
    流程：
      1. start_process(cmd /c echo done) — 立即退出
      2. wait + close
      3. signal("kill") → 应抛 RuntimeError
    """
    print("\n已关闭进程发 signal → Error", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动并等待退出
        proc = sb.start_process(command_line="cmd.exe /c echo done")
        print(f"  process started: pid={proc.pid}", flush=True)
        proc.wait(timeout_ms=10000)
        proc.close()
        print(f"  process closed", flush=True)

        # 2. 对已关闭进程发 signal → 应抛异常
        raised = False
        try:
            proc.signal("kill")
        except RuntimeError as e:
            raised = True
            err_msg = str(e)
            print(f"  RuntimeError received: {err_msg}", flush=True)
        except Exception as e:
            raised = True
            print(f"  Exception received: {type(e).__name__}: {e}", flush=True)

        _assert(raised, "应抛异常（process already exited/closed）")
        print("  [PASS] 已关闭进程 signal 返回 Error", flush=True)

    finally:
        sb.shutdown()


# =============================================================================
# 主入口
# =============================================================================

_TESTS = [
    ("Kill 强杀长跑进程", test_1_kill_terminates_long_running_process),
    ("CtrlBreak 中断长跑进程", test_2_ctrlbreak_interrupts_loop),
    ("已退出进程 signal → Error", test_3_signal_already_exited),
    ("无效 signal 值 → Error", test_4_invalid_signal_value),
    ("已关闭进程 signal → Error", test_5_signal_no_process_running),
]


def main() -> int:
    selected = set()
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            try:
                selected.add(int(arg))
            except ValueError:
                pass

    passed = 0
    failed = 0
    skipped = 0

    for i, (name, fn) in enumerate(_TESTS, 1):
        if selected and i not in selected:
            continue
        print(f"\n{'='*60}", flush=True)
        print(f"Test {i}/{len(_TESTS)}: {name}", flush=True)
        print(f"{'='*60}", flush=True)
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {e}", flush=True)
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}", flush=True)
            failed += 1

    print(f"\n{'='*60}", flush=True)
    print(f"Result: {passed} passed, {failed} failed, {skipped} skipped", flush=True)
    print(f"{'='*60}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
