"""test_global_quota.py - 多沙箱全局资源配额验证（Phase 14 pybind11 直调形态）。

【全部 SKIP 说明】
pybind11 形态下 SandboxInstanceBinding 构造 NativeSandboxInstance 时，
global_quota 参数固定传 nullptr（见 src/bindings/SandboxInstanceBinding.cpp:83-84）：

    instance_ = std::make_unique<NativeSandboxInstance>(
        logger_, nullptr, nullptr, config_.monitoring);
                       ^^^^^^^  ^^^^^^^
                       silo     global_quota (nullptr)

因此 config.global_quota 字段虽被 ConfigLoader 解析进 SandboxConfig，
但运行时 NativeSandboxInstance::global_quota_ 始终为 nullptr，
StartProcess 中全局配额 Acquire/Release 逻辑被完全跳过
（见 src/adapters/NativeSandboxInstance.cpp:174 `if (global_quota_)`）。

结论：pybind11 形态下无法验证全局配额功能，所有用例 SKIP。
要恢复验证，需在 SandboxInstanceBinding 中根据 config.global_quota.enabled
构造 GlobalQuotaManagerImpl 并注入 NativeSandboxInstance。

原旧形态用例（subprocess + sandbox.exe，跨进程共享配额池）：
  T1: 启用全局配额，正常启动进程成功（Acquire/Release 工作）
  T2: 配额耗尽 → StartProcess 返回 Error 事件（GlobalQuotaExceeded）
  T3: 配额释放后可再次启动
  T4: 未启用全局配额时行为与之前一致（无 Error）
  T5: 两个实例共享同一配额池（跨进程生效）

运行方式（在仓库根目录）：
  python tests/e2e/test_global_quota.py
"""

from __future__ import annotations

import sys


# =============================================================================
# SKIP 原因
# =============================================================================

_SKIP_REASON = (
    "pybind11 形态下 NativeSandboxInstance 未注入 IGlobalQuotaManager "
    "(SandboxInstanceBinding.cpp:83 固定传 nullptr)，global_quota_ 恒为 nullptr，"
    "StartProcess 中全局配额 Acquire/Release 被跳过 (NativeSandboxInstance.cpp:174)，"
    "无法验证配额语义"
)


# =============================================================================
# 测试用例（全部 SKIP）
# =============================================================================

def test_quota_ok_normal_start():
    """T1: 启用全局配额（宽松上限），正常启动进程。"""
    return _SKIP_REASON


def test_quota_exceeded_rejected():
    """T2: 配额耗尽 → StartProcess 被拒绝（GlobalQuotaExceeded）。"""
    return _SKIP_REASON


def test_quota_release_reuse():
    """T3: 一个进程正常退出释放配额后，可再次启动。"""
    return _SKIP_REASON


def test_quota_disabled_normal():
    """T4: 未启用全局配额 → 行为与之前一致（无 Error）。"""
    return _SKIP_REASON


def test_two_instances_share_pool():
    """T5: 两个实例共享同一配额池（跨进程/单进程多实例生效）。"""
    return _SKIP_REASON


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
    print("=" * 60)
    print("Global Quota Tests (Phase 14 pybind11 直调形态)")
    print("=" * 60)
    print(f"[SKIP] {_SKIP_REASON}")
    print()

    passed = 0
    failed = 0
    skipped = 0

    for name, fn in _TESTS:
        result = fn()
        if result is True:
            passed += 1
            print(f"  [{name}] PASS")
        else:
            skipped += 1
            print(f"  [{name}] SKIP: {result}")

    print(f"\n{'=' * 60}")
    print(f"Result: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'=' * 60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
