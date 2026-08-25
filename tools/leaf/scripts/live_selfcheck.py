"""真实 leaf 进程自检：外层 ConPTY 宿主跑 leaf 本体，模拟用户操作序列。

覆盖场景：dir 输出、窄化（裂行）、调大（恢复）、稳定帧，
逐帧检查宿主画面无裂行碎片、无内容三连重复。需要 Windows + ConPTY。
运行：python scripts/live_selfcheck.py
"""

import os
import re
import sys
import threading
import time

LEAF_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LEAF_DIR)

# 用共享 vendored 引擎（与 leaf 运行时一致）
from leaf.drivers import _engine
_engine.ensure_engine()

import pywezterm

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

def plain(s):
    return ANSI.sub("", s)

HOST_COLS, HOST_ROWS = 155, 42
host = pywezterm.Terminal(cols=HOST_COLS, rows=HOST_ROWS, scrollback=2000)
pty = pywezterm.Pty(cols=HOST_COLS, rows=HOST_ROWS)
stop = threading.Event()

def pump():
    while not stop.is_set():
        data = pty.read(4096, 0.05)
        if data:
            host.feed(data)

threading.Thread(target=pump, daemon=True).start()

def wait_text(out, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if out in host.text():
            return True
        time.sleep(0.05)
    return False

ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(extra) if extra else ""))
    ok = ok and cond

try:
    pty.spawn(["python", os.path.join(LEAF_DIR, "main.py"),
               "--pane1", "cmd.exe", "/d", "/k",
               "--pane2", "cmd.exe", "/d", "/c", "echo PANE2_OK"],
              cwd=LEAF_DIR)
    check("leaf 启动画面", wait_text("PANE2_OK"), "右窗格就绪")

    # pane 的 cwd 与宿主环境有关，先 cd 到 leaf 目录再 dir，断言不依赖本机内容
    pty.write(('cd /d "{}"\r'.format(LEAF_DIR)).encode())
    pty.write(b"dir\r")
    check("dir 输出", wait_text("conftest.py", 20))
    time.sleep(0.5)

    def scan(tag):
        lines = [l for l in plain(host.text()).splitlines() if l.strip()
                 and not l.strip().startswith("│") and " F9 " not in l]
        frags = [l.strip() for l in lines if "conftest.py" in l and len(l.strip()) < 33]
        tri = [lines[i].strip()[:20] for i in range(2, len(lines)) if lines[i] == lines[i-1] == lines[i-2]]
        print(f"[{tag}] 内容行={len(lines)} 碎片={frags[:2]} 三连重复={tri[:1]}")
        return not frags and not tri

    check("稳定画面无碎片", scan("base"))

    pty.resize(20, 20)
    time.sleep(1.2)
    check("窄化渲染无碎片无重复", scan("narrow"))

    pty.resize(HOST_COLS, HOST_ROWS)
    time.sleep(1.2)
    check("调大后渲染无碎片", scan("wide-back"))

    for i in range(3):
        time.sleep(0.15)
        check(f"稳定帧{i}", scan("stable"))
finally:
    stop.set()
    try:
        pty.kill()
    except Exception:
        pass
    pty.close()

print("RESULT:", "ALL PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)