"""交互录制黑盒自检：真实 leaf 进程（非 headless）+ 宿主 ConPTY 输入。

验证：用户输入命令 → leaf 渲染 → 录制捕获输出与输入 → 回放还原。
"""

import os
import re
import sys
import threading
import time

LEAF_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LEAF_DIR)

from leaf.drivers import _engine
_engine.ensure_engine()

import pywezterm

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

def plain(s):
    return ANSI.sub("", s)

HOST_COLS, HOST_ROWS = 120, 30
ok = True

def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(extra) if extra else ""))
    ok = ok and cond

def wait_text(host, out, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if out in host.text():
            return True
        time.sleep(0.05)
    return False

cast_path = os.path.join(os.path.abspath("."), "_interactive.cast")

try:
    host = pywezterm.Terminal(cols=HOST_COLS, rows=HOST_ROWS, scrollback=5000)
    pty = pywezterm.Pty(cols=HOST_COLS, rows=HOST_ROWS)
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            data = pty.read(4096, 0.05)
            if data:
                host.feed(data)

    threading.Thread(target=pump, daemon=True).start()

    pty.spawn(
        ["python", os.path.join(LEAF_DIR, "main.py"),
         "--pane1", "cmd.exe", "/d", "/k",
         "--pane2", "cmd.exe", "/d", "/c", "echo PANE2_READY",
         "--record", cast_path,
         "--capture-input"],
        cwd=LEAF_DIR,
    )
    check("leaf 启动", wait_text(host, "PANE2_READY"))

    # 模拟用户在焦点 pane 输入命令
    pty.write(b"echo INTERACTIVE_MARK_777\r")
    check("用户输入回显", wait_text(host, "INTERACTIVE_MARK_777"))

    # 等录制写盘
    time.sleep(1.0)
    # 退出 leaf：发送 exit + 回车到焦点 pane（cmd /k 会退出）
    pty.write(b"exit\r")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if pty.try_wait() is not None:
                break
        except Exception:
            break
        time.sleep(0.1)
    stop.set()
    try:
        pty.kill()
    except Exception:
        pass
    pty.close()

    # 验证 cast 文件
    from leaf.adapters.castfile import open_from_path
    h, ver, events = open_from_path(cast_path)
    evs = list(events)
    out_text = "".join(e.data.data for e in evs if hasattr(e.data, "data") and isinstance(getattr(e.data, "data", None), str))
    in_text = "".join(e.data.data for e in evs if hasattr(e.data, "data") and isinstance(getattr(e.data, "data", None), str) and False)
    # 输出包含交互标记
    check("录制包含交互输出", "INTERACTIVE_MARK_777" in out_text)
    # 输入被记录（capture-input）
    from leaf.domain.asciicast import Input
    input_events = [e for e in evs if isinstance(e.data, Input)]
    check("录制包含输入事件", len(input_events) > 0, f"{len(input_events)} 个输入事件")
    if input_events:
        combined_in = "".join(e.data.data for e in input_events)
        check("输入包含 echo 命令", "echo" in combined_in)
        check("输入包含标记", "INTERACTIVE_MARK_777" in combined_in)

    # 回放
    from leaf.usecases.player import Player
    import io

    class FakeConsole:
        def wait_input(self, ms):
            return False
        def read_inputs(self):
            return []
        def resize(self, size):
            pass
        def restore(self):
            pass

    class BufOutput:
        def __init__(self):
            self.buf = io.StringIO()
        def write(self, s):
            self.buf.write(s)
        def flush(self):
            pass

    term = pywezterm.Terminal(h.cols, h.rows, scrollback=10000)
    out = BufOutput()
    player = Player(term, out, FakeConsole())
    finished = player._play_once(cast_path, speed=20.0, idle_time_limit=None,
                                 pause_on_markers=False, auto_resize=False)
    check("回放正常结束", finished)
    text = term.text()
    check("回放还原交互输出", "INTERACTIVE_MARK_777" in text)

finally:
    if os.path.exists(cast_path):
        os.unlink(cast_path)

print("RESULT:", "ALL PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)