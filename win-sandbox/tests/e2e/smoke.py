"""e2e smoke test - pybind11 直调形态基础冒烟。

测试场景：
  1. 加载 win_sandbox_native 模块
  2. 构造 SandboxInstance
  3. start_process + wait + close
  4. shutdown
  5. 验证退出码为 0
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "_native_helpers"))

from _native_helpers import make_sandbox, run_and_capture


def main() -> int:
    try:
        # 1. 构造 SandboxInstance
        print("[1] construct SandboxInstance ...", flush=True)
        t0 = time.monotonic()
        sb = make_sandbox(log_level="info")
        print(f"    constructed in {time.monotonic() - t0:.3f}s", flush=True)

        # 2. start_process
        print("[2] start_process ...", flush=True)
        t0 = time.monotonic()
        proc = sb.start_process(command_line="cmd.exe /c echo hello")
        print(f"    started in {time.monotonic() - t0:.3f}s, pid={proc.pid}", flush=True)

        # 3. wait
        print("[3] wait ...", flush=True)
        t0 = time.monotonic()
        exit_code, reason, usage = proc.wait(timeout_ms=10000)
        elapsed = time.monotonic() - t0
        print(f"    exited in {elapsed:.3f}s, code={exit_code}, reason={reason}", flush=True)

        proc.close()

        if exit_code != 0:
            print(f"[FAIL] expected exit code 0, got {exit_code}", flush=True)
            return 1

        # 4. shutdown
        print("[4] shutdown ...", flush=True)
        t0 = time.monotonic()
        sb.shutdown()
        print(f"    shutdown in {time.monotonic() - t0:.3f}s", flush=True)

        print(f"\n[OK] smoke test passed", flush=True)
        return 0

    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
