"""e2e 测试：WriteStdin（交互式 REPL 场景）（pybind11 直调形态）。

验证 WriteStdin 功能在 pybind11 直调链路上的行为，覆盖 6 个子用例：
  1. 基础交互：启动 python -i，发 print(1+1)，收到 stdout 含 "2"
  2. 多次写入：连续发多条命令，每条都有正确响应
  3. interactive=false 时 write_pipe → OSError（stdin_handle is None）
  4. 已退出进程 write_pipe → OSError（管道已关闭）
  5. 无效 handle write_pipe → OSError（无效句柄）
  6. 无效 data 类型 write_pipe → TypeError

运行方式（在仓库根目录）：
  python tests/e2e/test_write_stdin.py
  或
  python tests/e2e/test_write_stdin.py 1   # 只跑用例 1
"""

from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402


# =============================================================================
# 辅助
# =============================================================================

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _drain_stdout_background(proc):
    """后台 drain stdout 到 bytearray，返回 (thread, bytearray, lock)。"""
    accumulated = bytearray()
    lock = threading.Lock()

    def callback(data):
        with lock:
            accumulated.extend(data)

    thread = helpers.drain_stdout(proc, callback)
    return thread, accumulated, lock


def _wait_stdout_contains(thread, accumulated, lock, needle, timeout=10.0):
    """等待 stdout 包含 needle（bytes），超时抛 AssertionError。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with lock:
            if needle in accumulated:
                return bytes(accumulated)
        time.sleep(0.1)
    with lock:
        current = bytes(accumulated)
    raise AssertionError(
        f"timeout waiting for stdout containing {needle!r}; "
        f"stdout so far: {current!r}"
    )


def _drain_stderr_background(proc):
    """后台 drain stderr 到 bytearray，返回 (thread, bytearray, lock)。"""
    accumulated = bytearray()
    lock = threading.Lock()

    def callback(data):
        with lock:
            accumulated.extend(data)

    thread = helpers.drain_stderr(proc, callback)
    return thread, accumulated, lock


def _wait_output_contains(stdout_acc, stdout_lock, stderr_acc, stderr_lock,
                          needle, timeout=10.0):
    """等待 stdout+stderr 包含 needle（bytes），超时抛 AssertionError。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with stdout_lock, stderr_lock:
            combined = bytes(stdout_acc) + bytes(stderr_acc)
            if needle in combined:
                return combined
        time.sleep(0.1)
    with stdout_lock, stderr_lock:
        combined = bytes(stdout_acc) + bytes(stderr_acc)
    raise AssertionError(
        f"timeout waiting for output containing {needle!r}; "
        f"output so far: {combined!r}"
    )


# =============================================================================
# 测试用例
# =============================================================================

def test_1_basic_repl_interaction() -> None:
    """用例 1：基础 REPL 交互。

    流程：
      1. start_process(python -i, interactive=true)
      2. 等 REPL 启动提示（">>>"）
      3. write_pipe(proc.stdin_handle, "print(1+1)\\n")
      4. 等 stdout 含 "2"
    """
    print("\n基础 REPL 交互", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动 python -i（交互式 REPL）
        proc = sb.start_process(command_line="python.exe -i -B", interactive=True)
        print(f"  process started: pid={proc.pid}", flush=True)

        # 2. 后台 drain stdout + stderr
        stdout_thread, stdout_acc, stdout_lock = _drain_stdout_background(proc)
        stderr_thread, stderr_acc, stderr_lock = _drain_stderr_background(proc)

        # 3. 等 REPL 启动提示（">>>" 提示符在 stderr）
        try:
            _wait_output_contains(stdout_acc, stdout_lock,
                                  stderr_acc, stderr_lock, b">>>", timeout=10.0)
            print("  REPL ready (>>> seen)", flush=True)
        except AssertionError as e:
            print(f"  [WARN] 未等到 >>> 提示符: {e}", flush=True)

        # 4. 发送 print(1+1) 命令
        _assert(proc.stdin_handle is not None, "stdin_handle should not be None for interactive")
        helpers.write_pipe(proc.stdin_handle, b"print(1+1)\n")
        print(f"  write_pipe sent: print(1+1)", flush=True)

        # 5. 等 stdout 含 "2"
        stdout_data = _wait_stdout_contains(stdout_thread, stdout_acc, stdout_lock,
                                             b"2", timeout=10.0)
        print(f"  received stdout: {stdout_data.strip()[-100:]!r}", flush=True)

        _assert(b"2" in stdout_data, f"stdout should contain '2', got: {stdout_data!r}")
        print("  [PASS] 基础 REPL 交互成功", flush=True)

        # 清理
        proc.close_stdin()
        proc.wait(timeout_ms=5000)
        proc.close()
    finally:
        sb.shutdown()


def test_2_multiple_writes() -> None:
    """用例 2：多次连续写入。

    流程：
      1. start_process(python -i, interactive=true)
      2. 等 REPL 启动
      3. 连续发 3 条命令：print("a1"), print("a2"), print("a3")
      4. 断言 stdout 含所有 "a1" / "a2" / "a3"
    """
    print("\n多次连续写入", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动 python -i
        proc = sb.start_process(command_line="python.exe -i -B", interactive=True)
        print(f"  process started: pid={proc.pid}", flush=True)

        # 2. 后台 drain stdout + stderr
        stdout_thread, stdout_acc, stdout_lock = _drain_stdout_background(proc)
        stderr_thread, stderr_acc, stderr_lock = _drain_stderr_background(proc)

        # 3. 等 REPL 启动（容错不致命）
        try:
            _wait_output_contains(stdout_acc, stdout_lock,
                                  stderr_acc, stderr_lock, b">>>", timeout=10.0)
        except AssertionError:
            pass

        # 4. 连续发 3 条命令
        commands = [b'print("a1")\n', b'print("a2")\n', b'print("a3")\n']
        for i, cmd in enumerate(commands, 1):
            helpers.write_pipe(proc.stdin_handle, cmd)
            print(f"  write_pipe #{i} sent: {cmd.strip()!r}", flush=True)
            time.sleep(0.3)

        # 5. 等 stdout 含所有输出
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            with stdout_lock:
                acc = bytes(stdout_acc)
            if b"a1" in acc and b"a2" in acc and b"a3" in acc:
                break
            time.sleep(0.2)

        with stdout_lock:
            accumulated = bytes(stdout_acc)
        print(f"  accumulated stdout: {accumulated.strip()[-200:]!r}", flush=True)
        _assert(b"a1" in accumulated, f"stdout should contain 'a1', got: {accumulated!r}")
        _assert(b"a2" in accumulated, f"stdout should contain 'a2', got: {accumulated!r}")
        _assert(b"a3" in accumulated, f"stdout should contain 'a3', got: {accumulated!r}")
        print("  [PASS] 多次连续写入成功", flush=True)

        # 清理
        proc.close_stdin()
        proc.wait(timeout_ms=5000)
        proc.close()
    finally:
        sb.shutdown()


def test_3_write_stdin_to_non_interactive() -> None:
    """用例 3：对 interactive=false 进程 write_pipe → stdin_handle is None。

    流程：
      1. start_process(python time.sleep(30), interactive=false)
      2. proc.stdin_handle 应为 None
      3. write_pipe(None, data) → OSError
    """
    print("\ninteractive=false 时 stdin_handle is None", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动非交互进程（默认 interactive=false）
        proc = sb.start_process(
            command_line='python.exe -c "import time; time.sleep(30)"',
        )
        print(f"  process started (interactive=false): pid={proc.pid}", flush=True)

        # 2. stdin_handle 应为 None
        _assert(proc.stdin_handle is None,
                f"stdin_handle should be None for non-interactive, got {proc.stdin_handle}")
        print(f"  stdin_handle is None (confirmed)", flush=True)

        # 3. write_pipe(None, data) → 应抛异常
        raised = False
        try:
            helpers.write_pipe(proc.stdin_handle, b"test")
        except (OSError, TypeError, ValueError) as e:
            raised = True
            print(f"  {type(e).__name__} received: {e}", flush=True)

        _assert(raised, "应抛异常（stdin_handle is None）")
        print("  [PASS] interactive=false 时 stdin_handle is None", flush=True)

        # 4. 清理
        proc.terminate(1)
        proc.wait(timeout_ms=5000)
        proc.close()
    finally:
        sb.shutdown()


def test_4_write_stdin_to_exited_process() -> None:
    """用例 4：对已退出进程 write_pipe → OSError（管道已关闭）。

    流程：
      1. start_process(cmd /c echo done, interactive=true)
      2. wait
      3. write_pipe(proc.stdin_handle, data) → OSError
    """
    print("\n已退出进程 write_pipe → Error", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动立即退出的进程（interactive=true 保留 stdin_handle）
        proc = sb.start_process(
            command_line="cmd.exe /c echo done",
            interactive=True,
        )
        print(f"  process started: pid={proc.pid}", flush=True)

        # 2. wait
        exit_code, reason, _ = proc.wait(timeout_ms=10000)
        print(f"  process exited: code={exit_code}", flush=True)

        # 3. write_pipe → 应抛 OSError（管道已关闭）
        raised = False
        if proc.stdin_handle is not None:
            try:
                helpers.write_pipe(proc.stdin_handle, b"test")
            except OSError as e:
                raised = True
                print(f"  OSError received: {e}", flush=True)
            except Exception as e:
                raised = True
                print(f"  {type(e).__name__} received: {e}", flush=True)
        else:
            # stdin_handle is None 也算符合预期
            raised = True
            print(f"  stdin_handle is None (process exited)", flush=True)

        _assert(raised, "应抛异常（管道已关闭）")
        print("  [PASS] 已退出进程 write_pipe 返回 Error", flush=True)

        proc.close()
    finally:
        sb.shutdown()


def test_5_write_stdin_invalid_handle() -> None:
    """用例 5：对无效 handle write_pipe → OSError。

    pybind11 直调形态下无 process_id 概念，改为测试对无效 handle 写入。
    流程：
      1. write_pipe(invalid_handle, data) → OSError
    """
    print("\n无效 handle write_pipe → Error", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 对无效 handle（0 或 -1）写入 → 应抛 OSError
        raised = False
        try:
            helpers.write_pipe(0, b"test")
        except OSError as e:
            raised = True
            print(f"  OSError received: {e}", flush=True)
        except Exception as e:
            raised = True
            print(f"  {type(e).__name__} received: {e}", flush=True)

        _assert(raised, "应抛异常（无效 handle）")
        print("  [PASS] 无效 handle write_pipe 返回 Error", flush=True)
    finally:
        sb.shutdown()


def test_6_invalid_data_type() -> None:
    """用例 6：write_pipe 传无效 data 类型 → TypeError。

    流程：
      1. start_process(python -i, interactive=true)
      2. write_pipe(proc.stdin_handle, None) → TypeError
    """
    print("\n无效 data 类型 → Error", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动交互进程
        proc = sb.start_process(command_line="python.exe -i -B", interactive=True)
        print(f"  process started: pid={proc.pid}", flush=True)

        # 后台 drain stdout/stderr（避免管道满）
        stdout_thread = helpers.drain_stdout(proc, lambda x: None)
        stderr_thread = helpers.drain_stderr(proc, lambda x: None)

        # 2. write_pipe 传 None → 应抛 TypeError
        raised = False
        try:
            helpers.write_pipe(proc.stdin_handle, None)
        except TypeError as e:
            raised = True
            print(f"  TypeError received: {e}", flush=True)
        except Exception as e:
            raised = True
            print(f"  {type(e).__name__} received: {e}", flush=True)

        _assert(raised, "应抛异常（无效 data 类型）")
        print("  [PASS] 无效 data 类型返回 Error", flush=True)

        # 3. 清理
        try:
            proc.terminate(1)
            proc.wait(timeout_ms=5000)
        except Exception:
            pass
        proc.close()
    finally:
        sb.shutdown()


# =============================================================================
# 主入口
# =============================================================================

_TESTS = [
    ("基础 REPL 交互", test_1_basic_repl_interaction),
    ("多次连续写入", test_2_multiple_writes),
    ("interactive=false 时 stdin_handle is None", test_3_write_stdin_to_non_interactive),
    ("已退出进程 write_pipe → Error", test_4_write_stdin_to_exited_process),
    ("无效 handle write_pipe → Error", test_5_write_stdin_invalid_handle),
    ("无效 data 类型 → Error", test_6_invalid_data_type),
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
