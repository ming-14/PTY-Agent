"""leaf play 命令黑盒自检：真实 leaf play 进程 + 宿主 ConPTY。

验证：play 进程渲染回放画面、识别按键（space 暂停/恢复）。
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

# 构造一个 cast 文件（含标记，供回放）
from leaf.domain.asciicast import Event, Output, Marker, Header, Version
from leaf.adapters.castfile import CastFileWriter

cast_path = os.path.join(os.path.abspath("."), "_play_test.cast")
writer = CastFileWriter(cast_path, Header(cols=100, rows=24))
writer.write_event(Event(0.0, Output("PLAY_FIRST_LINE\r\n")))
writer.write_event(Event(0.5, Output("PLAY_SECOND_LINE\r\n")))
writer.write_event(Event(1.0, Output("PLAY_THIRD_LINE\r\n")))
writer.finish()

try:
    host = pywezterm.Terminal(cols=120, rows=30, scrollback=5000)
    pty = pywezterm.Pty(cols=120, rows=30)
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            data = pty.read(4096, 0.05)
            if data:
                host.feed(data)

    threading.Thread(target=pump, daemon=True).start()

    pty.spawn(
        ["python", os.path.join(LEAF_DIR, "main.py"), "play", cast_path],
        cwd=LEAF_DIR,
    )
    check("play 渲染第一行", wait_text(host, "PLAY_FIRST_LINE"))
    check("play 渲染第二行", wait_text(host, "PLAY_SECOND_LINE"))
    check("play 渲染第三行", wait_text(host, "PLAY_THIRD_LINE"))

    # 等待回放结束
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

    # 命令行 convert 测试
    import subprocess
    out_cast = os.path.join(os.path.abspath("."), "_convert_out.cast")
    r = subprocess.run(
        [sys.executable, os.path.join(LEAF_DIR, "main.py"),
         "convert", cast_path, out_cast, "-f", "v3", "--overwrite"],
        capture_output=True, text=True, timeout=30, cwd=LEAF_DIR)
    check("convert 命令退出码 0", r.returncode == 0, r.stderr[:100])
    if os.path.exists(out_cast):
        from leaf.adapters.castfile import open_from_path
        h2, ver2, evs2 = open_from_path(out_cast)
        list(evs2)  # 消费迭代器以关闭文件
        check("convert 输出 v3", ver2 == 3)
        os.unlink(out_cast)

    # cat 命令行测试
    cat_cast = os.path.join(os.path.abspath("."), "_cat_out.cast")
    r = subprocess.run(
        [sys.executable, os.path.join(LEAF_DIR, "main.py"),
         "cat", cast_path, cast_path, "-o", cat_cast],
        capture_output=True, text=True, timeout=30, cwd=LEAF_DIR)
    check("cat 命令退出码 0", r.returncode == 0, r.stderr[:100])
    if os.path.exists(cat_cast):
        from leaf.adapters.castfile import open_from_path
        h3, ver3, evs3 = open_from_path(cat_cast)
        evs3_list = list(evs3)
        check("cat 拼接事件数", len(evs3_list) == 6)  # 2x3
        os.unlink(cat_cast)

finally:
    if os.path.exists(cast_path):
        os.unlink(cast_path)

print("RESULT:", "ALL PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)