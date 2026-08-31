"""leaf asciinema 功能黑盒自检：真实 leaf 进程上录制 → 验证 cast → 回放。

用外层 ConPTY 宿主跑 leaf 本体（headless 模式），模拟用户在焦点 pane 输入
命令，退出后验证 cast 文件内容与回放还原。

运行：python scripts/asciinema_selfcheck.py
"""

import json
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

cast_path = os.path.join(os.path.abspath("."), "_selfcheck.cast")

try:
    # ===== 1. headless 录制 =====
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
         "--headless",
         "--pane1", "cmd.exe", "/d", "/c", "echo FIRST_LINE_OK && ping -n 2 127.0.0.1 >nul",
         "--pane2", "cmd.exe", "/d", "/c", "echo SECOND_LINE_OK",
         "--record", cast_path],
        cwd=LEAF_DIR,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if os.path.exists(cast_path) and os.path.getsize(cast_path) > 0:
            break
        time.sleep(0.2)
    check("录制文件已生成", os.path.exists(cast_path) and os.path.getsize(cast_path) > 0)
    time.sleep(2)

    # 解析 cast 文件
    from leaf.adapters.castfile import open_from_path
    h, ver, events = open_from_path(cast_path)
    evs = list(events)
    out_text = "".join(e.data.data for e in evs if hasattr(e.data, "data") and e.data and isinstance(e.data.data, str))
    print(f"[cast] version={ver} size={h.cols}x{h.rows} events={len(evs)}")
    check("cast 版本 v3", ver == 3)
    check("cast 记录到 pane1 输出", "FIRST_LINE_OK" in out_text, "pane1 输出")
    check("cast 记录到 pane2 输出", "SECOND_LINE_OK" in out_text, "pane2 输出")
    # 有时间轴事件
    times = [e.time for e in evs]
    check("cast 有时间戳", len(times) > 0 and all(t >= 0 for t in times))

    stop.set()
    try:
        pty.kill()
    except Exception:
        pass
    pty.close()

    # ===== 2. 回放 =====
    # 在内存 Terminal 里跑 player，验证还原屏幕文本
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
    check("回放还原 pane1 输出", "FIRST_LINE_OK" in text)
    check("回放还原 pane2 输出", "SECOND_LINE_OK" in text)
    check("回放输出 ANSI 序列", "\x1b[" in out.buf.getvalue())

    # ===== 3. convert =====
    from leaf.usecases.cast_ops import convert
    buf = io.BytesIO()
    convert(cast_path, buf, "raw", overwrite=True)
    buf.seek(0)
    raw = buf.getvalue()
    check("convert → raw 含头部", raw.startswith(b"\x1b[8;"))

    # ===== 4. cat =====
    from leaf.usecases.cast_ops import cat
    buf3 = io.BytesIO()
    cat([cast_path, cast_path], buf3, output_format="v3")
    buf3.seek(0)
    h3, ver3, events3 = __import__("leaf.domain.asciicast", fromlist=["open_cast"]).open_cast(
        (l + "\n" for l in buf3.getvalue().decode("utf-8").strip().split("\n")))
    evs3 = list(events3)
    check("cat 拼接事件数翻倍", len(evs3) == 2 * len(evs))

finally:
    if os.path.exists(cast_path):
        os.unlink(cast_path)

print("RESULT:", "ALL PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)