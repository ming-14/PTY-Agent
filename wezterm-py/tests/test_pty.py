# wezterm-py 库级自测：portable_pty 伪终端引擎 + 终端模型闭环
# 独立于任何调用方，仅验证库自身行为。
#
# 闭环要点：子进程可能向终端发起查询（如 \x1b[6n DSR 光标位置），
# 终端模型的应答位于 writer 捕获缓冲，必须回写 pty，否则子进程等待应答卡住。

import os
import time

import pywezterm


def _run(p, t, timeout=6.0):
    """读 pty → feed 终端 → 取终端应答回写 pty，直到 EOF，返回累积输出。
    子进程退出后管道可能仍有余量，须继续排空到 EOF（read 返回 b""）。"""
    out = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = p.read(4096, timeout=0.2)
        if chunk:
            out += chunk
            t.feed(chunk)
            resp = t.drain_written()
            if resp:
                p.write(resp)
        elif p.try_wait() is not None:
            # 子进程已退出：排空残余输出直到 EOF
            while time.time() < deadline:
                c = p.read(4096, timeout=0.3)
                if not c:
                    break
                out += c
                t.feed(c)
            break
    return out


def test_pty_echo():
    p = pywezterm.Pty(cols=80, rows=24)
    t = pywezterm.Terminal(cols=80, rows=24)
    shell = os.environ.get("COMSPEC", "cmd.exe")
    pid, handle = p.spawn([shell, "/c", "echo hello && echo world"])
    assert pid > 0, pid
    assert handle != 0, handle
    assert p.child_pid() == pid
    assert p.hpcon() is not None, "Windows 下应暴露 HPCON"
    out = _run(p, t)
    assert b"hello" in out, out
    assert b"world" in out, out
    assert p.try_wait() == 0, p.try_wait()
    p.close()


def test_pty_write_resize_exit():
    p = pywezterm.Pty(cols=80, rows=24)
    t = pywezterm.Terminal(cols=80, rows=24)
    shell = os.environ.get("COMSPEC", "cmd.exe")
    pid, handle = p.spawn([shell])
    p.resize(100, 30)
    assert p.get_size() == (100, 30), p.get_size()
    p.write(b"echo HELLO_FROM_PYWEZTERM\r\n")
    p.write(b"exit\r\n")
    out = _run(p, t)
    assert b"HELLO_FROM_PYWEZTERM" in out, out
    p.close()


def test_pty_kill():
    p = pywezterm.Pty(cols=80, rows=24)
    shell = os.environ.get("COMSPEC", "cmd.exe")
    pid, handle = p.spawn([shell, "/c", "ping 127.0.0.1 -n 30"])
    assert pid > 0
    p.kill()
    for _ in range(100):
        if p.try_wait() is not None:
            break
        time.sleep(0.05)
    assert p.try_wait() is not None
    p.close()


if __name__ == "__main__":
    test_pty_echo()
    test_pty_write_resize_exit()
    test_pty_kill()
    print("PTY TESTS PASSED")
