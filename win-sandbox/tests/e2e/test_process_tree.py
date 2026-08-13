"""e2e 测试：进程树管理（job_process_* 事件 + 退出码查询）（pybind11 直调形态）。

验证进程树管理在 pybind11 直调链路上的行为，覆盖 11 个子用例：
  1. 子进程创建事件 job_process_started（含 process_name/process_path/parent_pid）
  2. 主进程不重复（只走 process_started / process_exited，无 job_process_* 重复）
  3. 子进程正常退出 job_process_exited（exit_kind=normal, exit_code=0）
  4. 子进程异常退出 job_process_exited（exit_kind=abnormal, exit_code=7）
  5. 崩溃路径去重（crash_dummy：同一 pid 仅一次 job_process_exited）
  6. query_process_exit_code（运行中）：返回 (259, True)（STILL_ACTIVE）
  7. query_process_exit_code（已退出）：返回 (真实退出码, False)
  8. query_process_exit_code 错误路径（process_not_found / TypeError）
  9. 同步全链路
  10. pid 类型严格校验（float 拒绝）
  11. 跨实例 PID 隔离（创建两个 SandboxInstance）

设计要点：
  - job_process_* 事件通过 proc.on_job_process_started/on_job_process_exited 回调收集
  - 回调在 IOCP 线程中调用（持 GIL），回调内只做入队列
  - query_process_exit_code(pid) 返回 (exit_code, is_active) 元组，
    运行中 exit_code=259=STILL_ACTIVE、is_active=True；已退出为 (真实退出码, False)
  - 跨实例用例创建两个 SandboxInstance

运行方式（在仓库根目录）：
  python tests/e2e/test_process_tree.py
  或
  python tests/e2e/test_process_tree.py 1   # 只跑用例 1
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CRASH_DUMMY = _REPO_ROOT / "build" / "bin" / "crash_dummy.exe"


# =============================================================================
# 辅助
# =============================================================================

def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _wait_callback_event(events, pred, timeout=10.0):
    """轮询 events 列表直到 pred(ev) 为 True 或超时，返回匹配事件或 None。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ev in list(events):
            if pred(ev):
                return ev
        time.sleep(0.05)
    return None


def _start_with_callbacks(sb, command_line, **kwargs):
    """启动进程 + 注册 job_process 回调 + 后台 drain + 后台 wait。

    返回 (proc, job_started, job_exited, wait_result, wait_thread)。
    wait_result 是 [exit_code, reason, usage]（wait 完成后填充）。
    """
    job_started = []
    job_exited = []

    proc = sb.start_process(command_line=command_line, **kwargs)
    proc.on_job_process_started = job_started.append
    proc.on_job_process_exited = job_exited.append

    # 后台 drain stdout/stderr（避免管道满）
    stdout_thread = helpers.drain_stdout(proc, lambda x: None)
    stderr_thread = helpers.drain_stderr(proc, lambda x: None)

    # 后台 wait
    wait_result = [None, None, None]
    wait_done = threading.Event()

    def _wait():
        try:
            wait_result[0], wait_result[1], wait_result[2] = proc.wait(timeout_ms=30000)
        except Exception as e:
            wait_result[0] = e
        wait_done.set()

    wait_thread = threading.Thread(target=_wait, daemon=True)
    wait_thread.start()

    return proc, job_started, job_exited, wait_result, wait_done, wait_thread


def _finish_wait(proc, wait_result, wait_done, wait_thread):
    """等待 wait 线程结束 + proc.close。"""
    wait_done.wait(timeout=35.0)
    wait_thread.join(timeout=5.0)
    proc.close()
    if isinstance(wait_result[0], Exception):
        raise wait_result[0]
    return wait_result[0], wait_result[1], wait_result[2]


# =============================================================================
# 测试用例
# =============================================================================

def test_1_child_start_event() -> None:
    """用例 1：子进程创建事件 job_process_started。"""
    print("\njob_process_started 子进程创建事件", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        proc, job_started, job_exited, wait_result, wait_done, wait_thread = \
            _start_with_callbacks(sb, 'cmd.exe /c "ping -n 6 127.0.0.1 >nul"')

        main_pid = proc.pid

        # 等 ping 的 job_process_started
        started_ev = _wait_callback_event(
            job_started,
            lambda ev: "ping" in ev.get("process_name", "").lower(),
            timeout=15.0,
        )
        _assert(started_ev is not None, "未收到 ping 子进程 job_process_started")
        print(f"  子进程: {started_ev}", flush=True)

        _assert(started_ev.get("pid", 0) > 0, f"pid 应非 0，实际 {started_ev.get('pid')}")
        _assert(started_ev.get("pid") != main_pid,
                f"pid 应为子进程且 ≠ 主 pid {main_pid}，实际 {started_ev.get('pid')}")
        _assert(bool(started_ev.get("process_name")),
                f"process_name 应非空，实际 {started_ev.get('process_name')}")
        _assert("ping.exe" in (started_ev.get("process_name") or "").lower(),
                f"process_name 应为 ping.exe，实际 {started_ev.get('process_name')}")
        _assert(bool(started_ev.get("process_path")) and
                (started_ev.get("process_path") or "").lower().endswith("ping.exe"),
                f"process_path 应以 ping.exe 结尾，实际 {started_ev.get('process_path')}")
        _assert(started_ev.get("parent_pid") == main_pid,
                f"parent_pid 应为主进程 {main_pid}，实际 {started_ev.get('parent_pid')}")
        _assert(started_ev.get("timestamp_ms", 0) > 0,
                f"timestamp_ms 应非 0，实际 {started_ev.get('timestamp_ms')}")

        print("  [PASS] 子进程创建事件字段完整", flush=True)

        _finish_wait(proc, wait_result, wait_done, wait_thread)
    finally:
        sb.shutdown()


def test_2_main_process_no_duplicate() -> None:
    """用例 2：主进程不重复下发。"""
    print("\n主进程零重复事件", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        proc, job_started, job_exited, wait_result, wait_done, wait_thread = \
            _start_with_callbacks(sb, 'cmd.exe /c exit 0')

        main_pid = proc.pid

        _finish_wait(proc, wait_result, wait_done, wait_thread)

        print(f"  主 pid={main_pid} job_started={len(job_started)} "
              f"job_exited={len(job_exited)}", flush=True)

        # 主进程 pid 不得出现在任何 job_process_* 事件中
        main_dup_started = [ev for ev in job_started if ev.get("pid") == main_pid]
        main_dup_exited = [ev for ev in job_exited if ev.get("pid") == main_pid]
        _assert(len(main_dup_started) == 0,
                f"主 pid {main_pid} 不应出现在 job_process_started，"
                f"实际 {len(main_dup_started)}")
        _assert(len(main_dup_exited) == 0,
                f"主 pid {main_pid} 不应出现在 job_process_exited，"
                f"实际 {len(main_dup_exited)}")

        print("  [PASS] 主进程零重复事件", flush=True)
    finally:
        sb.shutdown()


def test_3_child_normal_exit() -> None:
    """用例 3：子进程正常退出 job_process_exited。

    注：Low IL 语义下 ping 的 ICMP 被拒（100% 丢包，exit 1 判 abnormal），
    子进程正常退出改用嵌套 cmd（cmd /c "cmd /c exit 0"）。
    """
    print("\n子进程正常退出", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        proc, job_started, job_exited, wait_result, wait_done, wait_thread = \
            _start_with_callbacks(sb, 'cmd.exe /c "cmd /c exit 0"')

        # 等内层 cmd 的 job_process_started
        started_ev = _wait_callback_event(
            job_started,
            lambda ev: "cmd" in ev.get("process_name", "").lower()
            and ev.get("pid") != proc.pid,
            timeout=10.0,
        )
        _assert(started_ev is not None, "未收到内层 cmd 子进程 job_process_started")
        child_pid = started_ev.get("pid", 0)
        print(f"  内层 cmd 子进程 pid={child_pid}", flush=True)

        # 等该 pid 的 job_process_exited
        exited_ev = _wait_callback_event(
            job_exited,
            lambda ev: ev.get("pid") == child_pid,
            timeout=15.0,
        )
        _assert(exited_ev is not None, "未收到内层 cmd 子进程 job_process_exited")
        print(f"  payload={exited_ev}", flush=True)

        _assert(exited_ev.get("pid") == child_pid,
                f"pid 应为 {child_pid}，实际 {exited_ev.get('pid')}")
        _assert(exited_ev.get("exit_kind") == "normal",
                f"exit_kind 应为 normal，实际 {exited_ev.get('exit_kind')}")
        _assert(exited_ev.get("exit_code") == 0,
                f"exit_code 应为 0，实际 {exited_ev.get('exit_code')}")
        _assert(exited_ev.get("timestamp_ms", 0) > 0,
                f"timestamp_ms 应非 0，实际 {exited_ev.get('timestamp_ms')}")

        print("  [PASS] 子进程正常退出事件正确", flush=True)

        _finish_wait(proc, wait_result, wait_done, wait_thread)
    finally:
        sb.shutdown()


def test_4_child_abnormal_exit() -> None:
    """用例 4：子进程异常退出 job_process_exited。"""
    print("\n子进程异常退出", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        proc, job_started, job_exited, wait_result, wait_done, wait_thread = \
            _start_with_callbacks(sb, 'cmd.exe /c "cmd /c exit 7"')

        main_pid = proc.pid

        # 等 exit_code=7 的 job_process_exited
        exited_ev = _wait_callback_event(
            job_exited,
            lambda ev: ev.get("exit_code") == 7,
            timeout=15.0,
        )
        _assert(exited_ev is not None, "未收到内层 cmd 的 job_process_exited")
        print(f"  payload={exited_ev}", flush=True)

        _assert((exited_ev.get("pid") or 0) != main_pid,
                "pid 应为子进程（≠ 主 pid）")
        _assert(exited_ev.get("exit_kind") == "abnormal",
                f"exit_kind 应为 abnormal，实际 {exited_ev.get('exit_kind')}")
        _assert(exited_ev.get("exit_code") == 7,
                f"exit_code 应为 7，实际 {exited_ev.get('exit_code')}")

        print("  [PASS] 子进程异常退出事件正确", flush=True)

        _finish_wait(proc, wait_result, wait_done, wait_thread)
    finally:
        sb.shutdown()


def test_5_crash_dedup() -> None:
    """用例 5：崩溃路径同一 pid 仅一次 job_process_exited。"""
    print("\n崩溃路径退出事件去重", flush=True)
    if not _CRASH_DUMMY.exists():
        print(f"  [SKIP] crash_dummy.exe 不存在: {_CRASH_DUMMY}", flush=True)
        return

    # crash_dummy.exe 的构建目录可能带显式 ACL（低 IL 进程不可读，cmd /c
    # 中转时由低 IL 打开 exe 会失败）。复制到 %LOCALAPPDATA%\win-sandbox\probe\
    # （无显式 ACL，低 IL 可读）再经 cmd /c 启动，保持「cmd 内层子进程崩溃」场景。
    probe_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "win-sandbox" / "probe"
    if not probe_dir.is_dir():
        print(f"  [SKIP] LOCALAPPDATA 缺失: {probe_dir}", flush=True)
        return
    probe_dir.mkdir(parents=True, exist_ok=True)
    crash_copy = probe_dir / "crash_dummy.exe"
    try:
        shutil.copy2(_CRASH_DUMMY, crash_copy)
    except OSError as e:
        print(f"  [SKIP] 复制 crash_dummy 失败: {e}", flush=True)
        return

    sb = make_sandbox(log_level="info")

    try:
        proc, job_started, job_exited, wait_result, wait_done, wait_thread = \
            _start_with_callbacks(
                sb, f'cmd.exe /c "{crash_copy}"',
                quota={"crash_silent": True},
            )

        main_pid = proc.pid

        _finish_wait(proc, wait_result, wait_done, wait_thread)

        print(f"  job_started={len(job_started)} job_exited={len(job_exited)}", flush=True)

        # 按 process_name 定位 crash_dummy 子进程
        crash_started = [ev for ev in job_started
                         if "crash_dummy" in ev.get("process_name", "").lower()]
        _assert(len(crash_started) >= 1,
                f"应收到 crash_dummy 的 job_process_started，实际 {len(crash_started)}")
        child_pid = crash_started[0].get("pid", 0)
        _assert(child_pid != main_pid, "子进程 pid 不应为主 pid")
        print(f"  crash_dummy pid={child_pid}", flush=True)

        # 同一 pid 的退出事件必须恰好 1 条（去重）
        child_exited = [ev for ev in job_exited if ev.get("pid") == child_pid]
        _assert(len(child_exited) == 1,
                f"崩溃子进程 pid={child_pid} 的 job_process_exited 应为 1 条，"
                f"实际 {len(child_exited)}")
        payload = child_exited[0]
        print(f"  崩溃子进程 pid={child_pid} payload={payload}", flush=True)
        _assert(payload.get("exit_kind") == "abnormal",
                f"exit_kind 应为 abnormal，实际 {payload.get('exit_kind')}")
        code = payload.get("exit_code", 0)
        _assert(code & 0xFFFFFFFF == 0xC0000005,
                f"崩溃退出码应为 0xC0000005，实际 {code}")

        print("  [PASS] 崩溃路径去重 + 退出码正确", flush=True)
    finally:
        sb.shutdown()


def test_6_query_exit_code_active() -> None:
    """用例 6：退出码查询（运行中）。

    主进程长跑中查询自身 pid：返回 (259, True)（STILL_ACTIVE）。
    """
    print("\nquery_process_exit_code（运行中）", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        proc = sb.start_process(command_line='cmd.exe /c "ping -n 6 127.0.0.1 >nul"')
        main_pid = proc.pid

        # 后台 drain（避免管道满）
        helpers.drain_stdout(proc, lambda x: None)
        helpers.drain_stderr(proc, lambda x: None)

        # 查询运行中进程的退出码 → (259, True) (STILL_ACTIVE)
        exit_code, is_active = proc.query_process_exit_code(main_pid)
        print(f"  query_process_exit_code(pid={main_pid}) = ({exit_code}, {is_active})",
              flush=True)

        _assert(exit_code == 259,
                f"运行中 exit_code 应为 259(STILL_ACTIVE)，实际 {exit_code}")
        _assert(is_active is True,
                f"运行中 is_active 应为 True，实际 {is_active}")

        print("  [PASS] 运行中返回 (259, True)", flush=True)

        proc.wait(timeout_ms=30000)
        proc.close()
    finally:
        sb.shutdown()


def test_7_query_exit_code_finished() -> None:
    """用例 7：退出码查询（已退出）。

    主进程退出后查询：返回真实退出码。
    """
    print("\nquery_process_exit_code（已退出）", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        proc = sb.start_process(command_line='cmd.exe /c "ping -n 3 127.0.0.1 >nul"')
        main_pid = proc.pid

        # drain
        helpers.drain_stdout(proc, lambda x: None)
        helpers.drain_stderr(proc, lambda x: None)

        # 等主进程退出
        exit_code, reason, _ = proc.wait(timeout_ms=15000)
        print(f"  主进程已退出 pid={main_pid} exit_code={exit_code}", flush=True)

        # 查询已退出进程的退出码
        # 注意：进程退出后进程对象存活窗口内可查询，窗口过后 OpenProcess 失败
        queried_code = None
        queried_active = None
        for attempt in range(3):
            try:
                queried_code, queried_active = proc.query_process_exit_code(main_pid)
                break
            except RuntimeError as e:
                print(f"  attempt={attempt} 查询失败: {e}", flush=True)
                time.sleep(0.05)

        _assert(queried_code is not None, "应在存活窗口内查到退出码")
        print(f"  query_process_exit_code(pid={main_pid}) = "
              f"({queried_code}, {queried_active})", flush=True)

        _assert(queried_code == exit_code,
                f"查询退出码应为 {exit_code}，实际 {queried_code}")
        _assert(queried_active is False,
                f"已退出 is_active 应为 False，实际 {queried_active}")

        print("  [PASS] 已退出返回 (真实退出码, False)", flush=True)
        proc.close()
    finally:
        sb.shutdown()


def test_8_query_exit_code_errors() -> None:
    """用例 8（错误路径）：process_not_found / TypeError。"""
    print("\nquery_process_exit_code 错误路径", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        proc = sb.start_process(command_line='cmd.exe /c "ping -n 6 127.0.0.1 >nul"')
        main_pid = proc.pid

        helpers.drain_stdout(proc, lambda x: None)
        helpers.drain_stderr(proc, lambda x: None)

        # 8a. pid 不属于本 Job（大号假 pid）→ RuntimeError (process_not_found)
        raised = False
        try:
            proc.query_process_exit_code(0x7FFFFFFF)
        except RuntimeError as e:
            raised = True
            err_msg = str(e)
            print(f"  8a: RuntimeError: {err_msg}", flush=True)
            _assert("not" in err_msg.lower() or "not_found" in err_msg.lower(),
                    f"8a: 应提示 process_not_found, got: {err_msg}")
        _assert(raised, "8a: 应抛 RuntimeError（process_not_found）")
        print("  [OK] pid 不属于本 Job → process_not_found", flush=True)

        # 8b. 缺 pid 参数（传 None）→ TypeError
        raised = False
        try:
            proc.query_process_exit_code(None)
        except (TypeError, RuntimeError) as e:
            raised = True
            print(f"  8b: {type(e).__name__}: {e}", flush=True)
        _assert(raised, "8b: 应抛异常（None pid）")
        print("  [OK] None pid → 异常", flush=True)

        # 8c. 负数 pid → 异常
        raised = False
        try:
            proc.query_process_exit_code(-1)
        except (TypeError, RuntimeError, ValueError) as e:
            raised = True
            print(f"  8c: {type(e).__name__}: {e}", flush=True)
        _assert(raised, "8c: 应抛异常（负数 pid）")
        print("  [OK] 负数 pid → 异常", flush=True)

        print("  [PASS] 三种错误路径全部正确", flush=True)

        proc.wait(timeout_ms=30000)
        proc.close()
    finally:
        sb.shutdown()


def test_9_sync_flow() -> None:
    """用例 9：同步全链路。"""
    print("\n同步全链路", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        job_started = []

        proc = sb.start_process(command_line='cmd.exe /c "ping -n 6 127.0.0.1 >nul"')
        proc.on_job_process_started = job_started.append
        main_pid = proc.pid

        helpers.drain_stdout(proc, lambda x: None)
        helpers.drain_stderr(proc, lambda x: None)

        # 等待子进程创建事件
        child_started = _wait_callback_event(
            job_started,
            lambda ev: ev.get("pid") != main_pid,
            timeout=10.0,
        )
        _assert(child_started is not None, "未收到 job_process_started")
        print(f"  子进程创建事件: {child_started}", flush=True)

        # 查询退出码（运行中）
        exit_code, is_active = proc.query_process_exit_code(main_pid)
        print(f"  查询响应: exit_code={exit_code} is_active={is_active}", flush=True)
        _assert(exit_code == 259,
                f"运行中应为 259，实际 {exit_code}")
        _assert(is_active is True,
                f"运行中 is_active 应为 True，实际 {is_active}")

        print("  [PASS] 同步全链路正确", flush=True)

        proc.wait(timeout_ms=30000)
        proc.close()
    finally:
        sb.shutdown()


def test_10_strict_pid_type_check() -> None:
    """用例 10：pid 类型严格校验。

    float 等非整数必须拒绝，不得静默截断。
    """
    print("\npid 类型严格校验", flush=True)
    sb = make_sandbox(log_level="info")

    try:
        proc = sb.start_process(command_line='cmd.exe /c "ping -n 6 127.0.0.1 >nul"')
        main_pid = proc.pid

        helpers.drain_stdout(proc, lambda x: None)
        helpers.drain_stderr(proc, lambda x: None)

        # float pid 必须拒绝
        cases = [
            ("pid float 拒绝", 1234.5),
            ("pid 科学计数法拒绝", 1e5),
        ]
        for desc, bad_pid in cases:
            raised = False
            try:
                proc.query_process_exit_code(bad_pid)
            except (TypeError, RuntimeError, ValueError) as e:
                raised = True
                print(f"  [OK] {desc} → {type(e).__name__}: {e}", flush=True)
            _assert(raised, f"{desc}: 应抛异常")

        print("  [PASS] 非整数类型全部拒绝", flush=True)

        proc.wait(timeout_ms=30000)
        proc.close()
    finally:
        sb.shutdown()


def test_11_cross_instance_pid_isolation() -> None:
    """用例 11：跨实例 PID 隔离。

    实例 2 查询实例 1 的主进程 pid：
    必须抛 RuntimeError（pid 不属于实例 2 的 Job），不得返回实例 1 进程的状态。
    """
    print("\n跨实例 PID 隔离", flush=True)

    sb1 = make_sandbox(log_level="info")
    try:
        proc1 = sb1.start_process(command_line='cmd.exe /c "ping -n 10 127.0.0.1 >nul"')
        main1 = proc1.pid
        helpers.drain_stdout(proc1, lambda x: None)
        helpers.drain_stderr(proc1, lambda x: None)

        sb2 = make_sandbox(log_level="info")
        try:
            proc2 = sb2.start_process(command_line='cmd.exe /c "ping -n 10 127.0.0.1 >nul"')
            main2 = proc2.pid
            helpers.drain_stdout(proc2, lambda x: None)
            helpers.drain_stderr(proc2, lambda x: None)

            print(f"  实例1: main_pid={main1}", flush=True)
            print(f"  实例2: main_pid={main2}", flush=True)

            # 实例 2 查询实例 1 的 pid → 必须 RuntimeError (process_not_found)
            raised = False
            try:
                proc2.query_process_exit_code(main1)
            except RuntimeError as e:
                raised = True
                err_msg = str(e)
                print(f"  跨实例查询: RuntimeError: {err_msg}", flush=True)
                _assert("not" in err_msg.lower() or "not_found" in err_msg.lower(),
                        f"跨实例查询应提示 process_not_found, got: {err_msg}")
            _assert(raised, "跨实例查询应抛 RuntimeError（pid 不属于实例2 的 Job）")

            # 对照：实例 2 查询自身主进程 pid → 正常成功
            exit_code, is_active = proc2.query_process_exit_code(main2)
            _assert(exit_code == 259,
                    f"自身查询应为 259(STILL_ACTIVE)，实际 {exit_code}")
            _assert(is_active is True,
                    f"自身查询 is_active 应为 True，实际 {is_active}")

            print("  [PASS] 跨实例拒绝 + 自身实例正常", flush=True)

            proc2.wait(timeout_ms=30000)
            proc2.close()
        finally:
            sb2.shutdown()
    finally:
        proc1.wait(timeout_ms=30000)
        proc1.close()
        sb1.shutdown()


# =============================================================================
# 主入口
# =============================================================================

_TESTS = [
    ("子进程创建事件 job_process_started", test_1_child_start_event),
    ("主进程零重复事件", test_2_main_process_no_duplicate),
    ("子进程正常退出", test_3_child_normal_exit),
    ("子进程异常退出", test_4_child_abnormal_exit),
    ("崩溃路径退出事件去重", test_5_crash_dedup),
    ("退出码查询（运行中）", test_6_query_exit_code_active),
    ("退出码查询（已退出）", test_7_query_exit_code_finished),
    ("退出码查询错误路径", test_8_query_exit_code_errors),
    ("同步全链路", test_9_sync_flow),
    ("pid 类型严格校验", test_10_strict_pid_type_check),
    ("跨实例 PID 隔离", test_11_cross_instance_pid_isolation),
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
        print(f"\n{'=' * 60}", flush=True)
        print(f"Test {i}/{len(_TESTS)}: {name}", flush=True)
        print(f"{'=' * 60}", flush=True)
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {e}", flush=True)
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}", flush=True)
            failed += 1

    print(f"\n{'=' * 60}", flush=True)
    print(f"Result: {passed} passed, {failed} failed, {skipped} skipped", flush=True)
    print(f"{'=' * 60}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
