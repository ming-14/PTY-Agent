"""test_global_quota.py - 多沙箱全局资源配额验证（pybind11 直调形态）。

验证全局配额在 pybind11 直调链路上的行为（SandboxInstanceBinding 按
config.global_quota.enabled 构造并注入 GlobalQuotaManagerImpl），覆盖 5 个子用例：
  1. 启用全局配额（宽松上限），正常启动进程（Acquire/Release 工作）
  2. 配额耗尽 → start_process 被拒绝（GlobalQuotaExceeded）
  3. 进程退出释放配额后，可再次启动（Release 生效）
  4. 未启用全局配额时行为与之前一致（无拒绝）
  5. 两个实例共享同一配额池（超额合计被拒）

运行方式（在仓库根目录）：
  python tests/e2e/test_global_quota.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]

_POOL = "win-sandbox-test-quota-pool"


def _quota_config(max_processes: int, pool: str = _POOL) -> str:
    """生成 global_quota 启用的配置 JSON 路径。"""
    cfg = {
        "global_quota": {
            "enabled": True,
            "pool_name": pool,
            "max_cpu_rate_percent": 100,
            "max_memory_mb": 4096,
            "max_processes": max_processes,
        }
    }
    path = Path(tempfile.gettempdir()) / f"bb_gq_{pool.replace('-', '_')}.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return str(path)


def _start_long(sb) -> object:
    """启动长跑进程（ping 20s），返回 proc。"""
    proc = sb.start_process(command_line='cmd.exe /c "ping -n 20 127.0.0.1 >nul"')
    helpers.drain_stdout(proc, lambda x: None)
    helpers.drain_stderr(proc, lambda x: None)
    return proc


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# =============================================================================
# 测试用例
# =============================================================================

def test_quota_ok_normal_start() -> object:
    """T1 启用全局配额（宽松上限），正常启动进程。"""
    cfg = _quota_config(max_processes=8)
    sb = make_sandbox(cfg)
    try:
        proc = _start_long(sb)
        _assert(proc.pid > 0, "进程应正常启动")
        proc.wait(timeout_ms=30000)
        proc.close()
        print("  [PASS] 宽松上限正常启动")
        return True
    finally:
        sb.shutdown()
        Path(cfg).unlink(missing_ok=True)


def test_quota_exceeded_rejected() -> object:
    """T2 配额耗尽 → start_process 被拒绝（GlobalQuotaExceeded）。"""
    cfg = _quota_config(max_processes=1)
    sb = make_sandbox(cfg)
    proc = None
    try:
        proc = _start_long(sb)  # 占满 1 个进程名额
        rejected = False
        try:
            sb.start_process(command_line="cmd.exe /c exit 0")
        except RuntimeError as e:
            rejected = True
            print(f"  [OK] 超限拒绝: {e}")
        _assert(rejected, "超限后 start_process 应抛异常（GlobalQuotaExceeded）")
        print("  [PASS] 超限拒绝")
        return True
    finally:
        if proc:
            try:
                proc.terminate(1)
                proc.wait(timeout_ms=5000)
            except Exception:
                pass
        sb.shutdown()
        Path(cfg).unlink(missing_ok=True)


def test_quota_release_reuse() -> object:
    """T3 进程退出释放配额后，可再次启动。"""
    cfg = _quota_config(max_processes=1)
    sb = make_sandbox(cfg)
    try:
        proc = _start_long(sb)
        proc.terminate(1)
        proc.wait(timeout_ms=5000)
        proc.close()
        # 释放后应能再次启动
        proc2 = sb.start_process(command_line="cmd.exe /c exit 0")
        proc2.wait(timeout_ms=10000)
        proc2.close()
        print("  [PASS] 释放后可复用")
        return True
    finally:
        sb.shutdown()
        Path(cfg).unlink(missing_ok=True)


def test_quota_disabled_normal() -> object:
    """T4 未启用全局配额 → 行为与之前一致（无拒绝）。"""
    sb = make_sandbox()
    proc = None
    try:
        proc = _start_long(sb)
        # 无配额限制：再启动一个也应成功
        proc2 = sb.start_process(command_line="cmd.exe /c exit 0")
        proc2.wait(timeout_ms=10000)
        proc2.close()
        print("  [PASS] 未启用时无拒绝")
        return True
    finally:
        if proc:
            try:
                proc.terminate(1)
                proc.wait(timeout_ms=5000)
            except Exception:
                pass
        sb.shutdown()


def test_two_instances_share_pool() -> object:
    """T5 两个实例共享同一配额池（合计超限被拒）。"""
    cfg = _quota_config(max_processes=2)  # 池上限 2
    sb1 = make_sandbox(cfg)
    sb2 = make_sandbox(cfg)

    procs = []
    try:
        # 实例 A 起 2 个（占满池）
        for _ in range(2):
            procs.append(_start_long(sb1))
        # 实例 B 再起 1 个 → 应被拒绝
        rejected = False
        try:
            procs.append(_start_long(sb2))
        except RuntimeError as e:
            rejected = True
            print(f"  [OK] 跨实例超限拒绝: {e}")
        _assert(rejected, "跨实例超限应被拒绝（池共享生效）")
        print("  [PASS] 跨实例共享池生效")
        return True
    finally:
        for p in procs:
            try:
                p.terminate(1)
                p.wait(timeout_ms=5000)
            except Exception:
                pass
        sb1.shutdown()
        sb2.shutdown()
        Path(cfg).unlink(missing_ok=True)


# =============================================================================
# 主入口
# =============================================================================

_TESTS = [
    ("T1_quota_ok_normal_start", test_quota_ok_normal_start),
    ("T2_quota_exceeded_rejected", test_quota_exceeded_rejected),
    ("T3_quota_release_reuse", test_quota_release_reuse),
    ("T4_quota_disabled_normal", test_quota_disabled_normal),
    ("T5_two_instances_share_pool", test_two_instances_share_pool),
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
            result = fn()
            if result is True:
                passed += 1
                print(f"  [{name}] PASS", flush=True)
            else:
                skipped += 1
                print(f"  [{name}] SKIP: {result}", flush=True)
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {e}", flush=True)
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {type(e).__name__}: {e}", flush=True)

    print(f"\n{'=' * 60}", flush=True)
    print(f"Result: {passed} passed, {failed} failed, {skipped} skipped", flush=True)
    print(f"{'=' * 60}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())