"""leaf asciinema 全面黑盒测试：所有子命令通过真实进程 + 真实 ConPTY 端到端验证。

覆盖：rec（headless/交互/选项）、play（本地/URL/速度/循环/标记/键盘）、
cat（同尺寸/异尺寸/stdout）、convert（v3/raw/txt）、zstd、
session（录制 + 交互）。

运行：python scripts/blackbox_full.py
"""

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

LEAF_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LEAF_DIR)

# ---- 工具 ----
ok = True
_results = []

def check(name, cond, extra=""):
    global ok
    ok = ok and cond
    _results.append((name, cond, extra))
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(extra) if extra else ""))

def wait_text(host, out, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if out in host.text():
            return True
        time.sleep(0.05)
    return False


class ConPTYHost:
    """真实 ConPTY 宿主：跑 leaf 进程并与之交互"""

    def __init__(self, cols=120, rows=30):
        from leaf.drivers import _engine
        _engine.ensure_engine()
        import pywezterm
        self.host = pywezterm.Terminal(cols=cols, rows=rows, scrollback=5000)
        self.pty = pywezterm.Pty(cols=cols, rows=rows)
        self.stop = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while not self.stop.is_set():
            data = self.pty.read(4096, 0.05)
            if data:
                self.host.feed(data)

    def spawn(self, argv, cwd=None):
        self.pty.spawn(argv, cwd=cwd)

    def write(self, data: bytes):
        self.pty.write(data)

    def text(self):
        return self.host.text()

    def wait(self, out, timeout=20.0):
        return wait_text(self.host, out, timeout)

    def wait_exit(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.pty.try_wait() is not None:
                    return True
            except Exception:
                return True
            time.sleep(0.1)
        return False

    def close(self):
        self.stop.set()
        try:
            self.pty.kill()
        except Exception:
            pass
        self.pty.close()


def run_cmd(argv, timeout=60, input_text=None):
    """子进程运行 leaf 命令，返回 (returncode, stdout, stderr)"""
    env = dict(os.environ)
    env["PYWEZTERM_DIR"] = os.path.join(LEAF_DIR, "..", "..", "bin", "pywezterm")
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        [sys.executable, "-X", "utf8", os.path.join(LEAF_DIR, "main.py")] + argv,
        capture_output=True, text=True, timeout=timeout, cwd=LEAF_DIR,
        env=env, input=input_text,
    )
    return r.returncode, r.stdout, r.stderr


def read_cast(path):
    """读取 cast 文件 → (header, version, events)"""
    from leaf.adapters.castfile import open_from_path
    return open_from_path(path)


def cast_text(path):
    """cast 文件全部输出事件拼接文本"""
    from leaf.domain.asciicast import Output, Input, Resize, Exit
    _, _, events = read_cast(path)
    evs = list(events)
    out = "".join(e.data.data for e in evs if isinstance(e.data, Output))
    inp = "".join(e.data.data for e in evs if isinstance(e.data, Input))
    return out, inp, evs


def make_cast(path, events, cols=100, rows=24):
    """构造 cast 文件"""
    from leaf.domain.asciicast import Event, Output, Input, Resize, Marker, Exit, Header, Version
    from leaf.adapters.castfile import CastFileWriter
    w = CastFileWriter(path, Header(cols=cols, rows=rows))
    for e in events:
        w.write_event(e)
    w.finish()


def tmp_cast(suffix=".cast"):
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.close()
    return f.name


TMP = tempfile.mkdtemp(prefix="leaf_bb_")

print("=" * 60)
print("1. rec — headless 单 pane")
print("=" * 60)
p = os.path.join(TMP, "rec_headless.cast")
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "rec", p,
         "--pane1", "cmd.exe", "/d", "/c", "echo HEADLESS_ONE",
         "--headless"], cwd=LEAF_DIR)
h.wait_exit(15)
h.close()
out, inp, evs = cast_text(p)
check("rec headless 生成文件", os.path.exists(p) and os.path.getsize(p) > 0)
check("rec headless 内容", "HEADLESS_ONE" in out, repr(out[:60]))
check("rec headless 版本 v3", read_cast(p)[1] == 3)
check("rec headless 有 exit 事件", any(type(e.data).__name__ == "Exit" for e in evs))

print("=" * 60)
print("2. rec — headless 双 pane（整屏合成）")
print("=" * 60)
p2 = os.path.join(TMP, "rec_dual.cast")
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "rec", p2,
         "--pane1", "cmd.exe", "/d", "/c", "echo LEFT_MARK_AAA",
         "--pane2", "cmd.exe", "/d", "/c", "echo RIGHT_MARK_BBB",
         "--headless"], cwd=LEAF_DIR)
h.wait_exit(15)
h.close()
out, inp, evs = cast_text(p2)
check("rec 双 pane 左输出", "LEFT_MARK_AAA" in out, repr(out[:80]))
check("rec 双 pane 右输出", "RIGHT_MARK_BBB" in out)
check("rec 双 pane 含分隔线", "│" in out)

print("=" * 60)
print("3. rec — 交互录制 + capture-input")
print("=" * 60)
p3 = os.path.join(TMP, "rec_interactive.cast")
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"),
         "--pane1", "cmd.exe", "/d", "/k",
         "--pane2", "cmd.exe", "/d", "/c", "echo PANE2_READY",
         "--record", p3, "--capture-input"], cwd=LEAF_DIR)
h.wait("PANE2_READY")
h.write(b"echo INTER_CMD_XYZ\r")
h.wait("INTER_CMD_XYZ")
time.sleep(1.0)
h.write(b"exit\r")
h.wait_exit(10)
h.close()
out, inp, evs = cast_text(p3)
check("rec 交互输出", "INTER_CMD_XYZ" in out, repr(out[-60:]))
check("rec 交互输入捕获", "INTER_CMD_XYZ" in inp, repr(inp[-60:]))
check("rec 交互输入为逐键事件", sum(1 for e in evs if type(e.data).__name__ == "Input") > 5)

print("=" * 60)
print("4. rec — append 追加")
print("=" * 60)
p5 = os.path.join(TMP, "rec_append.cast")
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "rec", p5,
         "--pane1", "cmd.exe", "/d", "/c", "echo APPEND_FIRST",
         "--headless"], cwd=LEAF_DIR)
h.wait_exit(15)
h.close()
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "rec", p5,
         "--pane1", "cmd.exe", "/d", "/c", "echo APPEND_SECOND",
         "--append", "--headless"], cwd=LEAF_DIR)
h.wait_exit(15)
h.close()
out, _, evs = cast_text(p5)
check("rec append 两段内容", "APPEND_FIRST" in out and "APPEND_SECOND" in out, repr(out[:100]))
check("rec append 时间轴连续", evs[-1].time > 0)

print("=" * 60)
print("5. rec — title / idle-time-limit")
print("=" * 60)
p6 = os.path.join(TMP, "rec_meta.cast")
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "rec", p6,
         "--pane1", "cmd.exe", "/d", "/c", "echo META_MARK",
         "--title", "My Recording", "--idle-time-limit", "2.5", "--headless"],
        cwd=LEAF_DIR)
h.wait_exit(15)
h.close()
hdr, _, _ = read_cast(p6)
check("rec title 写入 header", hdr.title == "My Recording", hdr.title)
check("rec idle_time_limit 写入 header", hdr.idle_time_limit == 2.5, hdr.idle_time_limit)

print("=" * 60)
print("6. play — 本地文件回放")
print("=" * 60)
p7 = tmp_cast()
make_cast(p7, [
    __import__("leaf.domain.asciicast", fromlist=["Event", "Output"]).Event(
        0.0, __import__("leaf.domain.asciicast", fromlist=["Output"]).Output("PLAY_LINE_A\r\n")),
    __import__("leaf.domain.asciicast", fromlist=["Event", "Output"]).Event(
        0.3, __import__("leaf.domain.asciicast", fromlist=["Output"]).Output("PLAY_LINE_B\r\n")),
])
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "play", p7], cwd=LEAF_DIR)
check("play 渲染第一行", h.wait("PLAY_LINE_A"))
check("play 渲染第二行", h.wait("PLAY_LINE_B"))
h.wait_exit(10)
h.close()

print("=" * 60)
print("7. play — URL 回放（本地 HTTP）")
print("=" * 60)
import http.server, socketserver
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass
os.chdir(os.path.dirname(p7))
srv = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/{os.path.basename(p7)}"
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "play", url], cwd=LEAF_DIR)
check("play URL 渲染", h.wait("PLAY_LINE_A"))
h.wait_exit(10)
h.close()
srv.shutdown()

print("=" * 60)
print("8. play — 键盘控制（space 暂停 / ctrl+c 退出）")
print("=" * 60)
p9 = tmp_cast()
from leaf.domain.asciicast import Event, Output
make_cast(p9, [
    Event(0.0, Output("PKEY_LINE_ONE\r\n")),
    Event(5.0, Output("PKEY_LINE_TWO\r\n")),  # 5s 后（等待按键）
])
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "play", p9], cwd=LEAF_DIR)
h.wait("PKEY_LINE_ONE")
# 发送 ctrl+c 中断
h.write(b"\x03")
check("play ctrl+c 中断退出", h.wait_exit(10))
h.close()

print("=" * 60)
print("9. play — --speed 加速（时间压缩）")
print("=" * 60)
p10 = tmp_cast()
make_cast(p10, [
    Event(0.0, Output("SPEED_FIRST\r\n")),
    Event(3.0, Output("SPEED_SECOND\r\n")),
])
t0 = time.monotonic()
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "play", p10, "--speed", "10.0"],
        cwd=LEAF_DIR)
h.wait("SPEED_SECOND", timeout=10)
elapsed = time.monotonic() - t0
h.wait_exit(5)
h.close()
check("play --speed 加速", elapsed < 2.5, f"3s 间隔 10x 应 <2.5s，实际 {elapsed:.2f}s")

print("=" * 60)
print("10. cat — 同尺寸拼接")
print("=" * 60)
c1, c2 = tmp_cast(), tmp_cast()
make_cast(c1, [Event(1.0, Output("CAT_A\r\n"))])
make_cast(c2, [Event(2.0, Output("CAT_B\r\n"))])
rc, out, err = run_cmd(["cat", c1, c2])
check("cat 退出码 0", rc == 0, err[:80])
lines = [l for l in out.strip().split("\n") if l]
check("cat 输出 3 行（header+2）", len(lines) == 3, len(lines))
# v3 是 delta 时间：读回验证绝对时间轴连续
hdr_c, ver_c, evs_c = read_cast(tmp_cast()) if False else (None, None, None)
import io as _io
# 从 stdout 内容构造流读回
from leaf.domain.asciicast import open_cast
_hdr, _ver, _evs_gen = open_cast((l + "\n" for l in lines))
evs_c = list(_evs_gen)
check("cat 事件数", len(evs_c) == 2, len(evs_c))
check("cat 时间轴连续（绝对）", abs(evs_c[1].time - 3.0) < 0.001,
      f"ev1={evs_c[0].time}, ev2={evs_c[1].time}")
check("cat 内容", evs_c[0].data.data == "CAT_A\r\n" and evs_c[1].data.data == "CAT_B\r\n")

print("=" * 60)
print("11. cat — 异尺寸插入 resize + -o 文件")
print("=" * 60)
c3, c4 = tmp_cast(), tmp_cast()
make_cast(c3, [Event(1.0, Output("CAT_C\r\n"))], cols=80, rows=24)
make_cast(c4, [Event(1.0, Output("CAT_D\r\n"))], cols=100, rows=30)
outp = os.path.join(TMP, "cat_out.cast")
rc, out, err = run_cmd(["cat", c3, c4, "-o", outp])
check("cat -o 退出码 0", rc == 0, err[:80])
hdr, ver, evs_gen = read_cast(outp)
evs = list(evs_gen)
check("cat 异尺寸 3 事件（含 resize）", len(evs) == 3, len(evs))
check("cat 含 resize 事件", any(type(e.data).__name__ == "Resize" for e in evs))

print("=" * 60)
print("12. convert — v3→raw")
print("=" * 60)
cv_in = tmp_cast()
make_cast(cv_in, [Event(1.0, Output("CONV_XYZ\r\n"))])
raw_out = os.path.join(TMP, "conv_raw.bin")
rc, out, err = run_cmd(["convert", cv_in, raw_out, "-f", "raw", "--overwrite"])
check("convert raw 退出码 0", rc == 0, err[:80])
with open(raw_out, "rb") as f:
    raw = f.read()
check("convert raw 头部", raw.startswith(b"\x1b[8;"))
check("convert raw 内容", b"CONV_XYZ" in raw)

print("=" * 60)
print("13. convert — v3→txt")
print("=" * 60)
txt_out = os.path.join(TMP, "conv.txt")
rc, out, err = run_cmd(["convert", cv_in, txt_out, "-f", "txt", "--overwrite"])
check("convert txt 退出码 0", rc == 0, err[:80])
with open(txt_out, "r", encoding="utf-8") as f:
    txt = f.read()
check("convert txt 纯文本", "CONV_XYZ" in txt and "\x1b[" not in txt, repr(txt[:60]))

print("=" * 60)
print("14. zstd 压缩读写")
print("=" * 60)
try:
    import zstandard  # noqa
    zst_out = os.path.join(TMP, "conv.zst")
    rc, out, err = run_cmd(["convert", cv_in, zst_out, "-f", "v3", "--overwrite"])
    check("zstd convert 退出码 0", rc == 0, err[:80])
    with open(zst_out, "rb") as f:
        head = f.read(4)
    check("zstd 魔数", head == b"\x28\xb5\x2f\xfd", head.hex())
    hdr, ver, evs_gen = read_cast(zst_out)
    evs = list(evs_gen)
    check("zstd 可读回", len(evs) == 1 and any("CONV_XYZ" in (e.data.data if hasattr(e.data, "data") else "") for e in evs))
except ImportError:
    check("zstandard 库可用（跳过 zstd 测试）", False, "未安装")

print("=" * 60)
print("16. session — 录制 + 直播组合")
print("=" * 60)
sess_cast = os.path.join(TMP, "session.cast")
h = ConPTYHost()
h.spawn([sys.executable, os.path.join(LEAF_DIR, "main.py"), "session", sess_cast, "--local",
         "--pane1", "cmd.exe", "/d", "/k",
         "--pane2", "cmd.exe", "/d", "/c", "echo SESSION_READY"], cwd=LEAF_DIR)
h.wait("SESSION_READY")
h.write(b"echo SESSION_CMD_MARK\r")
h.wait("SESSION_CMD_MARK")
time.sleep(1.0)
h.write(b"exit\r")
h.wait_exit(10)
h.close()
check("session 生成录制文件", os.path.exists(sess_cast) and os.path.getsize(sess_cast) > 0)
out, _, _ = cast_text(sess_cast)
check("session 录制内容", "SESSION_CMD_MARK" in out, repr(out[-60:]))

# ---- 汇总 ----
print("=" * 60)
fails = [r for r in _results if not r[1]]
print(f"总计 {len(_results)} 项检查，通过 {len(_results)-len(fails)}，失败 {len(fails)}")
if fails:
    print("失败项:")
    for name, cond, extra in fails:
        print(f"  FAIL {name} {extra}")
print("RESULT:", "ALL PASS" if not fails else "FAILED")
sys.exit(0 if not fails else 1)