# wezterm-py 库级边界自测：CJK 宽字符 / UTF-8 中文 / 大输出 / 异常 / close 幂等

import os
import sys
import time

import pywezterm


def _run(p, t, timeout=8.0):
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


def test_cjk_wide_width():
    """CJK 双宽字符：text 无占位空格，snapshot 宽字符占 2 格"""
    t = pywezterm.Terminal(cols=10, rows=2)
    t.feed("我喜欢你".encode())
    assert t.text() == "我喜欢你", t.text()
    snap = t.snapshot()
    row0 = snap[0]
    widths = [c[9] for c in row0]
    assert sum(widths) == 8, widths
    # 首个 cell 依次为 我喜 欢 你；续 cell ch 为空
    chars = [c[1] for c in row0 if c[1]]
    assert chars == ["我", "喜", "欢", "你"], chars


def test_utf8_chinese_pty():
    """UTF-8 中文经 pty 往返（python 子进程输出到 ConPTY 恒为 UTF-8）"""
    p = pywezterm.Pty(cols=80, rows=24)
    t = pywezterm.Terminal(cols=80, rows=24)
    p.spawn([sys.executable, "-c", "print('你好世界')"])
    out = _run(p, t)
    assert "你好世界".encode("utf-8") in out, out
    # 终端模型可见文本包含中文（无 CJK 占位空格问题）
    assert "你好世界" in t.text(), t.text()
    p.close()


def test_large_output():
    """大输出（10 万字符）不丢不卡"""
    p = pywezterm.Pty(cols=80, rows=24)
    t = pywezterm.Terminal(cols=80, rows=24)
    p.spawn([sys.executable, "-c", "print('x' * 100000)"])
    out = _run(p, t)
    assert out.count(b"x") >= 100000, len(out)
    p.close()


def test_spawn_failure():
    """spawn 不存在的程序应抛异常"""
    p = pywezterm.Pty(cols=80, rows=24)
    try:
        p.spawn([r"C:\nonexistent_program_xyz_12345.exe"])
        raise AssertionError("spawn 应抛异常")
    except Exception:
        pass
    p.close()


def test_close_idempotent():
    """close 幂等；close 前先排空剩余输出，之后 read 返回空"""
    p = pywezterm.Pty(cols=80, rows=24)
    shell = os.environ.get("COMSPEC", "cmd.exe")
    p.spawn([shell, "/c", "echo hi"])
    # 排空输出
    for _ in range(20):
        if not p.read(4096, timeout=0.2):
            break
    p.close()
    p.close()  # 幂等
    assert p.read(100, timeout=0.1) == b""
    assert p.get_size() == (0, 0)


if __name__ == "__main__":
    test_cjk_wide_width()
    test_utf8_chinese_pty()
    test_large_output()
    test_spawn_failure()
    test_close_idempotent()
    print("EDGE TESTS PASSED")
