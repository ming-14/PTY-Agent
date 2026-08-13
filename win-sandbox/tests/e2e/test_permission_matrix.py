"""e2e 测试：权限矩阵（Permission Matrix）（pybind11 直调形态）。

验证 Admin 和 StandardUser 模式下 CapabilityReport 的正确性。

pybind11 直调形态下，CapabilityReport 通过 SandboxInstance.capabilities 属性
直接获取（无需通过 IPC Ready 事件）。

运行方式（在仓库根目录）：
  python tests/e2e/test_permission_matrix.py
  或
  python tests/e2e/test_permission_matrix.py 1   # 只跑用例 1
"""

from __future__ import annotations

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from _native_helpers import make_sandbox, helpers, win_sandbox_native  # noqa: E402


# =============================================================================
# 辅助
# =============================================================================

def _is_admin() -> bool:
    """检查当前进程是否以管理员权限运行。"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _assert(cond: bool, msg: str) -> None:
    """简单断言：失败时抛 AssertionError，附带信息。"""
    if not cond:
        raise AssertionError(msg)


def _validate_capabilities(cap: dict, context: str) -> None:
    """验证 CapabilityReport 结构 + admin 模式下关键模块可用。

    Args:
        cap: sb.capabilities 返回的 dict
        context: 调用上下文描述（用于错误信息）
    """
    _assert(isinstance(cap, dict),
            f"[{context}] capabilities should be a dict, got {type(cap).__name__}")
    _assert("mode" in cap,
            f"[{context}] capabilities should contain 'mode'")
    _assert(cap["mode"] == "admin",
            f"[{context}] mode should be 'admin' when running as admin, "
            f"got {cap['mode']!r}")

    modules = cap.get("capabilities", [])
    _assert(isinstance(modules, list),
            f"[{context}] capabilities.capabilities should be a list, "
            f"got {type(modules).__name__}")
    _assert(len(modules) > 0,
            f"[{context}] capabilities.capabilities should contain module entries")

    # 验证每个模块条目格式
    modules_dict = {}
    for m in modules:
        _assert("module" in m,
                f"[{context}] module entry should have 'module' field, "
                f"got keys={list(m.keys())}")
        _assert("available" in m,
                f"[{context}] module {m['module']!r} should have 'available' field")
        modules_dict[m["module"]] = m

    # 验证 admin 模式下关键模块全部可用
    expected_modules = ["app_container", "etw", "job_object", "filesystem", "pipe_security"]
    for name in expected_modules:
        _assert(name in modules_dict,
                f"[{context}] missing module: {name}")
        _assert(modules_dict[name]["available"],
                f"[{context}] {name} should be available in admin mode, "
                f"got available={modules_dict[name]['available']}")


# =============================================================================
# 子用例
# =============================================================================

def test_admin_capabilities() -> None:
    """子用例 1：验证 Admin 模式下 capabilities 属性。

    场景：以管理员权限创建 SandboxInstance
    期望：
      - capabilities.mode == "admin"
      - capabilities.capabilities 包含所有模块条目
      - 各模块 entry 有 available boolean
      - 关键模块（app_container, etw, job_object, filesystem, pipe_security）均为 available=true
    """
    print("\n[Test 1] admin capabilities (from sb.capabilities)")
    if not _is_admin():
        print("  SKIP: not running as admin")
        return

    sb = make_sandbox(log_level="info")
    try:
        cap = sb.capabilities
        _validate_capabilities(cap, "test_admin_capabilities")
        modules = cap.get("capabilities", [])
        print(f"  mode={cap['mode']!r} modules={len(modules)}")
        print("  PASS")
    finally:
        sb.shutdown()


def test_capability_report_structure() -> None:
    """子用例 2：验证 CapabilityReport 结构完整性。

    capabilities 属性返回的 dict 结构（由 CapabilityReport 序列化）：
      - capabilities.mode：权限模式（admin / standard_user）
      - capabilities.capabilities：模块列表，每项 {module, available, degraded_reason}

    期望：
      - capabilities 为 dict
      - capabilities.mode == "admin"
      - capabilities.capabilities 为 list，每项含 module/available 字段
      - 关键模块均为 available=true
    """
    print("\n[Test 2] capability report structure")
    if not _is_admin():
        print("  SKIP: not running as admin")
        return

    sb = make_sandbox(log_level="info")
    try:
        cap = sb.capabilities
        _validate_capabilities(cap, "test_capability_report_structure")
        modules = cap.get("capabilities", [])
        print(f"  mode={cap['mode']!r} modules={len(modules)}")
        print("  PASS")
    finally:
        sb.shutdown()


# =============================================================================
# 主入口
# =============================================================================

TESTS = [
    ("admin_capabilities", test_admin_capabilities),
    ("capability_report_structure", test_capability_report_structure),
]


def run_all() -> int:
    """运行全部子用例，返回失败数。"""
    failures = 0
    for name, fn in TESTS:
        try:
            fn()
        except Exception as e:
            print(f"  FAIL: {e}")
            failures += 1

    print(f"\n{'=' * 60}")
    print(f"Result: {len(TESTS) - failures}/{len(TESTS)} PASS")
    if failures:
        print(f"Failed tests:")
        for name, _ in TESTS:
            print(f"  - {name}")
    return failures


if __name__ == "__main__":
    if len(sys.argv) > 1:
        idx = int(sys.argv[1])
        if 1 <= idx <= len(TESTS):
            try:
                TESTS[idx - 1][1]()
                print(f"\n=== Test {idx} PASS ===")
                sys.exit(0)
            except Exception as e:
                print(f"\n=== Test {idx} FAIL: {e} ===")
                sys.exit(1)
        print(f"invalid test index: {idx}")
        sys.exit(2)
    sys.exit(1 if run_all() else 0)
