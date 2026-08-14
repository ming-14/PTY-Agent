# 验证：wezterm-py 使用侧载 OpenConsole.exe（非系统 conhost）+ close 无死锁
import os
import subprocess
import sys
import time

import pywezterm


def snapshot():
    """返回 (OpenConsole 进程数, conhost 进程数) 快照"""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq OpenConsole.exe"],
        capture_output=True, text=True,
    ).stdout
    oc = out.lower().count("openconsole.exe")
    out2 = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq conhost.exe"],
        capture_output=True, text=True,
    ).stdout
    ch = out2.lower().count("conhost.exe")
    return oc, ch


def main():
    before = snapshot()
    print(f"before: OpenConsole={before[0]} conhost={before[1]}", flush=True)

    p = pywezterm.Pty(cols=80, rows=24)
    shell = os.environ.get("COMSPEC", "cmd.exe")
    p.spawn([shell, "/c", "echo HI_FROM_WEZTERM_PY"])
    time.sleep(1.0)
    after_spawn = snapshot()
    print(f"after spawn: OpenConsole={after_spawn[0]} conhost={after_spawn[1]}", flush=True)

    out = p.read(65536, timeout=2.0)
    print(f"output contains marker: {b'HI_FROM_WEZTERM_PY' in out}", flush=True)

    t0 = time.time()
    p.close()
    dt = time.time() - t0
    print(f"close returned in {dt:.3f}s", flush=True)

    time.sleep(1.0)
    after_close = snapshot()
    print(f"after close: OpenConsole={after_close[0]} conhost={after_close[1]}", flush=True)

    spawned_oc = after_spawn[0] > before[0]
    no_conhost_new = after_spawn[1] <= before[1]
    close_ok = dt < 5.0
    cleanup_ok = after_close[0] <= after_spawn[0]
    print(f"RESULT: used_OpenConsole={spawned_oc} no_new_conhost={no_conhost_new} close_ok={close_ok} cleanup_ok={cleanup_ok}", flush=True)
    ok = spawned_oc and no_conhost_new and close_ok and cleanup_ok
    print("PASS" if ok else "FAIL", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
