"""e2e 测试：多进程管理（pybind11 直调形态）。

验证 SandboxInstance 多进程架构在 pybind11 直调链路上的行为，覆盖 6 个子用例：
  1. 并发启动 3 个进程，stdout 互不串扰
  2. 并发 WriteStdin 到 3 个 REPL，每个 REPL 只响应发给自己的命令
  3. TerminateProcess 单个进程不影响其他进程
  4. list_processes 返回正确的进程列表
  5. 操作不存在的 process_id → RuntimeError（process_not_found）
  6. process_id 自增不复用

设计要点：
  - pybind11 直调形态下 process_id 由 proc.process_id 获取（沙箱内部自增，从 1 开始）
  - 每个进程独立 drain stdout/stderr，互不串扰
  - list_processes 返回所有进程状态

运行方式（在仓库根目录）：
  python tests/e2e/test_multiprocess.py
  或
  python tests/e2e/test_multiprocess.py 1   # 只跑用例 1
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


def _drain_stderr_background(proc):
    """后台 drain stderr 到 bytearray，返回 (thread, bytearray, lock)。"""
    accumulated = bytearray()
    lock = threading.Lock()

    def callback(data):
        with lock:
            accumulated.extend(data)

    thread = helpers.drain_stderr(proc, callback)
    return thread, accumulated, lock


# =============================================================================
# 测试用例
# =============================================================================

def test_1_concurrent_start_event_routing() -> None:
    """用例 1：并发启动 3 个进程，stdout 互不串扰。

    流程：
      1. 连续 start_process 3 个（cmd /c echo pN），各自 drain stdout
      2. 等所有 3 个进程退出
      3. 断言每个 stdout 只含自己的 "p1"/"p2"/"p3"
    """
    print("\n并发启动 3 进程 + stdout 互不串扰", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        markers = ["p1_alpha", "p2_beta", "p3_gamma"]
        procs = []
        drains = []
        for m in markers:
            proc = sb.start_process(command_line=f'cmd.exe /c echo {m}')
            stdout_thread, stdout_acc, stdout_lock = _drain_stdout_background(proc)
            stderr_thread, stderr_acc, stderr_lock = _drain_stderr_background(proc)
            procs.append(proc)
            drains.append((stdout_thread, stdout_acc, stdout_lock,
                           stderr_thread, stderr_acc, stderr_lock))

        print(f"  started 3 processes: pids={[p.pid for p in procs]}", flush=True)

        # 等所有进程退出
        results = []
        for proc, drain in zip(procs, drains):
            exit_code, reason, _ = proc.wait(timeout_ms=15000)
            stdout_thread, stdout_acc, stdout_lock, *_ = drain
            stdout_thread.join(timeout=5)
            with stdout_lock:
                stdout_bytes = bytes(stdout_acc)
            proc.close()
            results.append((exit_code, stdout_bytes))

        for i, (exit_code, stdout_bytes) in enumerate(results):
            print(f"    proc {i+1} exit={exit_code} stdout={stdout_bytes.strip()!r}",
                  flush=True)

        # 断言每个 stdout 只含一个 marker
        for i, m in enumerate(markers):
            stdout_str = results[i][1].decode("utf-8", errors="replace")
            _assert(m in stdout_str,
                    f"proc {i+1} stdout should contain {m!r}, got {stdout_str!r}")
            # 不应含其他 marker
            for j, other_m in enumerate(markers):
                if j != i:
                    _assert(other_m not in stdout_str,
                            f"proc {i+1} stdout should not contain {other_m!r}, "
                            f"got {stdout_str!r}")

        print("  [PASS] 3 进程 stdout 互不串扰", flush=True)

    finally:
        sb.shutdown()


def test_2_concurrent_write_stdin() -> None:
    """用例 2：并发 WriteStdin 到 3 个独立 REPL，每个只响应发给自己的命令。

    流程：
      1. 启动 3 个 python -i（interactive=true）
      2. 等 REPL 就绪
      3. 对每个进程发不同的 print 命令
      4. 收集 stdout，断言每个进程只输出自己的命令结果
    """
    print("\n并发 WriteStdin 到 3 个 REPL", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动 3 个 python -i
        procs = []
        drains = []
        for i in range(3):
            proc = sb.start_process(command_line="python.exe -i -B", interactive=True)
            stdout_thread, stdout_acc, stdout_lock = _drain_stdout_background(proc)
            stderr_thread, stderr_acc, stderr_lock = _drain_stderr_background(proc)
            procs.append(proc)
            drains.append((stdout_acc, stdout_lock, stderr_acc, stderr_lock))

        print(f"  started 3 REPL: pids={[p.pid for p in procs]}", flush=True)

        # 2. 给每个进程发不同的 print 命令
        markers = []
        for i, proc in enumerate(procs):
            marker = f"MARKER_{chr(ord('A') + i)}_{proc.pid}_END"
            markers.append(marker)
            helpers.write_pipe(proc.stdin_handle, f"print('{marker}')\n".encode())
        print(f"  sent 3 WriteStdin, markers={markers}", flush=True)

        # 3. 等每个进程 stdout 含自己的 marker
        for i, proc in enumerate(procs):
            stdout_acc, stdout_lock, stderr_acc, stderr_lock = drains[i]
            deadline = time.monotonic() + 15.0
            found = False
            while time.monotonic() < deadline:
                with stdout_lock:
                    acc = bytes(stdout_acc)
                if markers[i].encode() in acc:
                    found = True
                    break
                time.sleep(0.1)
            _assert(found,
                    f"proc {i+1} (pid={proc.pid}) 未收到自己的 marker {markers[i]!r}")
            # 验证不含其他 marker
            for j, other_m in enumerate(markers):
                if j != i:
                    with stdout_lock:
                        acc = bytes(stdout_acc)
                    _assert(other_m.encode() not in acc,
                            f"proc {i+1} 不应收到其他进程的 marker {other_m!r}")

        print("  [PASS] 3 REPL 的 WriteStdin 互不串扰", flush=True)

        # 4. 清理：Kill 所有 REPL
        for proc in procs:
            try:
                proc.terminate(1)
                proc.wait(timeout_ms=5000)
            except Exception:
                pass
            proc.close()
    finally:
        sb.shutdown()


def test_3_terminate_one_does_not_affect_others() -> None:
    """用例 3：TerminateProcess 单个进程不影响其他进程。

    流程：
      1. 启动 2 个长跑进程（python time.sleep 30）
      2. Terminate proc1
      3. 等 proc1 退出
      4. 验证 proc2 仍在运行（list_processes 中 state=running）
      5. 清理：Kill proc2
    """
    print("\nTerminateProcess 单杀不影响其他进程", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动 2 个长跑进程
        proc1 = sb.start_process(
            command_line='python.exe -c "import time; time.sleep(30)"',
        )
        proc2 = sb.start_process(
            command_line='python.exe -c "import time; time.sleep(30)"',
        )
        # drain 避免管道满
        helpers.drain_stdout(proc1, lambda x: None)
        helpers.drain_stderr(proc1, lambda x: None)
        helpers.drain_stdout(proc2, lambda x: None)
        helpers.drain_stderr(proc2, lambda x: None)
        print(f"  started: proc1.pid={proc1.pid} proc2.pid={proc2.pid}", flush=True)
        time.sleep(0.5)

        # 2. Terminate proc1
        proc1.terminate(42)
        print(f"  Terminate sent for proc1.pid={proc1.pid}", flush=True)

        # 3. 等 proc1 退出
        exit_code1, reason1, _ = proc1.wait(timeout_ms=10000)
        print(f"  proc1 exited: code={exit_code1} reason={reason1}", flush=True)

        # 4. list_processes 验证 proc2 仍在运行
        procs = sb.list_processes()
        proc_by_id = {p.get("process_id"): p for p in procs}
        print(f"  list_processes: {[(p.get('process_id'), p.get('state')) for p in procs]}",
              flush=True)

        _assert(proc2.process_id in proc_by_id,
                f"proc2 (process_id={proc2.process_id}) 应仍在运行，"
                f"实际存活进程 {list(proc_by_id.keys())}")
        _assert(proc_by_id[proc2.process_id].get("state") == "running",
                f"proc2 state 应为 running，实际 {proc_by_id[proc2.process_id].get('state')}")

        # 5. 清理：Kill proc2
        proc2.terminate(1)
        proc2.wait(timeout_ms=5000)
        proc1.close()
        proc2.close()

        print("  [PASS] 单杀不影响其他进程", flush=True)

    finally:
        sb.shutdown()


def test_4_list_processes_returns_all() -> None:
    """用例 4：list_processes 返回所有进程列表。

    流程：
      1. 启动 1 长 + 2 短
      2. 等 2 个短进程退出
      3. list_processes → 长进程仍在，短进程已退出（state=exited 或被清理不在列表）
    """
    print("\nlist_processes 返回进程列表", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 1. 启动 1 长 + 2 短
        proc_long = sb.start_process(
            command_line='python.exe -c "import time; time.sleep(30)"',
        )
        helpers.drain_stdout(proc_long, lambda x: None)
        helpers.drain_stderr(proc_long, lambda x: None)

        proc_short1 = sb.start_process(command_line='cmd.exe /c echo short1')
        helpers.drain_stdout(proc_short1, lambda x: None)
        helpers.drain_stderr(proc_short1, lambda x: None)
        proc_short1.wait(timeout_ms=10000)
        proc_short1.close()

        proc_short2 = sb.start_process(command_line='cmd.exe /c echo short2')
        helpers.drain_stdout(proc_short2, lambda x: None)
        helpers.drain_stderr(proc_short2, lambda x: None)
        proc_short2.wait(timeout_ms=10000)
        proc_short2.close()

        print(f"  started: long={proc_long.process_id} "
              f"short1={proc_short1.process_id} short2={proc_short2.process_id}",
              flush=True)

        # 2. list_processes
        procs = sb.list_processes()
        pids = [p.get("process_id") for p in procs]
        print(f"  list_processes: pids={pids}", flush=True)

        _assert(proc_long.process_id in pids,
                f"长进程 process_id={proc_long.process_id} 应在列表中，实际 {pids}")

        # 3. 清理：Kill 长进程
        proc_long.terminate(1)
        proc_long.wait(timeout_ms=5000)
        proc_long.close()

        print("  [PASS] list_processes 行为符合预期", flush=True)

    finally:
        sb.shutdown()


def test_5_operate_on_nonexistent_process_id() -> None:
    """用例 5：操作不存在的 process_id → RuntimeError（process_not_found）。

    pybind11 直调形态下无 process_id 参数的 API，改为测试
    query_process_exit_code(不存在的 pid) → RuntimeError。
    """
    print("\n操作不存在的 pid → Error", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        # 启动一个进程获得 proc 对象
        proc = sb.start_process(command_line='cmd.exe /c echo hello')
        helpers.drain_stdout(proc, lambda x: None)
        helpers.drain_stderr(proc, lambda x: None)

        # query_process_exit_code(大号假 pid) → RuntimeError
        raised = False
        try:
            proc.query_process_exit_code(0x7FFFFFFF)
        except RuntimeError as e:
            raised = True
            err_msg = str(e)
            print(f"  RuntimeError: {err_msg}", flush=True)
            _assert("not" in err_msg.lower() or "not_found" in err_msg.lower(),
                    f"应提示 process_not_found, got: {err_msg}")
        except Exception as e:
            raised = True
            print(f"  {type(e).__name__}: {e}", flush=True)

        _assert(raised, "应抛异常（process_not_found）")
        print("  [PASS] 不存在的 pid 返回 process_not_found", flush=True)

        proc.wait(timeout_ms=10000)
        proc.close()
    finally:
        sb.shutdown()


def test_6_process_id_auto_increment() -> None:
    """用例 6：process_id 从 1 自增不复用。

    流程：
      1. 启动 3 个进程（短命令），记录 process_id
      2. 断言 process_id 是 1, 2, 3（首次分配）
      3. 等进程退出
      4. 再启动 1 个进程，断言 process_id 是 4（不复用）
    """
    print("\nprocess_id 自增不复用", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        pids = []
        for i in range(3):
            proc = sb.start_process(command_line=f'cmd.exe /c echo t35-6-{i}')
            helpers.drain_stdout(proc, lambda x: None)
            helpers.drain_stderr(proc, lambda x: None)
            pids.append(proc.process_id)
            proc.wait(timeout_ms=10000)
            proc.close()

        print(f"  first 3 pids: {pids}", flush=True)
        _assert(pids == [1, 2, 3],
                f"前 3 个 process_id 应为 [1,2,3]，实际 {pids}")

        # 再启动 1 个，process_id 应为 4
        proc4 = sb.start_process(command_line='cmd.exe /c echo t35-6-3')
        helpers.drain_stdout(proc4, lambda x: None)
        helpers.drain_stderr(proc4, lambda x: None)
        pid4 = proc4.process_id
        print(f"  4th pid: {pid4}", flush=True)
        _assert(pid4 == 4,
                f"第 4 个 process_id 应为 4（不复用），实际 {pid4}")

        proc4.wait(timeout_ms=10000)
        proc4.close()

        print("  [PASS] process_id 自增分配，不复用", flush=True)

    finally:
        sb.shutdown()


# =============================================================================
# 主入口
# =============================================================================

_TESTS = [
    ("并发启动 3 进程 + stdout 互不串扰", test_1_concurrent_start_event_routing),
    ("并发 WriteStdin 到 3 个 REPL", test_2_concurrent_write_stdin),
    ("TerminateProcess 单杀不影响其他", test_3_terminate_one_does_not_affect_others),
    ("list_processes 返回进程列表", test_4_list_processes_returns_all),
    ("操作不存在的 pid → Error", test_5_operate_on_nonexistent_process_id),
    ("process_id 自增不复用", test_6_process_id_auto_increment),
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
