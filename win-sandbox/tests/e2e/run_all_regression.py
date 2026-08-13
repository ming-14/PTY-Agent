"""全量 e2e 回归脚本：逐个运行测试套件并汇总结果。

排除 test_etw_admin.py（需管理员权限）。
"""
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

EXCLUDE = {"test_etw_admin.py"}

tests = sorted(
    p for p in (_REPO / "tests" / "e2e").glob("test_*.py")
    if p.name not in EXCLUDE
)

# 子进程强制 UTF-8 输出（replace 容错）：测试输出可能含被替换的非法字节，
# GBK 控制台下 print 会抛 UnicodeEncodeError 导致整套测试误报失败
_child_env = dict(os.environ, PYTHONIOENCODING="utf-8:replace", PYTHONUTF8="1")

results = []
for t in tests:
    print(f"\n===== {t.name} =====", flush=True)
    r = subprocess.run(
        [sys.executable, str(t)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=600,
        env=_child_env,
    )
    ok = r.returncode == 0
    results.append((t.name, ok, r))
    print(f"  -> {'PASS' if ok else 'FAIL'} (exit={r.returncode})", flush=True)
    if not ok:
        # 打印失败尾部输出
        tail = (r.stdout or "")[-3000:]
        print(tail, flush=True)

print(f"\n{'=' * 60}", flush=True)
passed = sum(1 for _, ok, _ in results if ok)
print(f"Result: {passed}/{len(results)} PASS", flush=True)
for name, ok, _ in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}", flush=True)
sys.exit(0 if passed == len(results) else 1)
