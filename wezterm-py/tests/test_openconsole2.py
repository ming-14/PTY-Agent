# 验证 OpenConsole 下：输出捕获 + close 后进程清理
import os
import subprocess
import sys
import time

import pywezterm


def count(name):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}"], capture_output=True, text=True).stdout
    return out.lower().count(name.lower())


def main():
    before_oc = count("OpenConsole.exe")
    p = pywezterm.Pty(cols=80, rows=24)
    shell = os.environ.get("COMSPEC", "cmd.exe")
    p.spawn([shell, "/c", "echo HI_FROM_WEZTERM_PY"])
    print("spawned", flush=True)

    # 循环读取输出（OpenConsole 冷启动 + cmd 启动需要时间）
    out = b""
    deadline = time.time() + 6.0
    while time.time() < deadline:
        chunk = p.read(4096, timeout=0.3)
        if chunk:
            out += chunk
            if b"HI_FROM_WEZTERM_PY" in out:
                break
    print(f"captured len={len(out)} marker={'HI_FROM_WEZTERM_PY' in out.decode('utf-8', 'replace')}", flush=True)
    if out:
        print(f"raw[:200]={out[:200]!r}", flush=True)

    p.close()
    print("closed", flush=True)

    # 观察 OpenConsole 是否随 close 退出
    for i in range(10):
        time.sleep(0.5)
        now = count("OpenConsole.exe")
        if now <= before_oc:
            print(f"OpenConsole cleaned up after {i+1} polls", flush=True)
            break
    else:
        print("OpenConsole STILL RUNNING after close", flush=True)


if __name__ == "__main__":
    main()
