# 验证 pywezterm 模型在 resize 后 scrollback 的完整性（leaf 同款依赖）
# 用法: python scripts/verify_pywezterm_reflow.py
import sys, os
_BIN = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'bin'))
sys.path.insert(0, os.path.join(_BIN, 'pywezterm'))
import pywezterm

def check(tag, term, expect_substr):
    sb = term.render_scrollback(keep_ansi=True)
    # 去 ANSI
    import re
    plain = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', sb)
    lines = [l for l in plain.split('\r\n') if l]
    found = [l for l in lines if expect_substr in l]
    # 检查是否有"疑似拆分残留"（行尾空格 + 下一行行首空格）
    merged = 0
    for i in range(len(lines) - 1):
        if lines[i].endswith(' ') and lines[i + 1].startswith(' '):
            merged += 1
    print(f"[{tag}] scrollback_lines={len(lines)} "
          f"expect_'{expect_substr}': {'OK' if found else 'MISSING'} "
          f"split_residue={merged}")
    if found:
        for l in found[:3]:
            print(f"    -> {l!r}")
    return found, merged

def main():
    t = pywezterm.Terminal(80, 24, scrollback=30000)  # 与 daemon 一致
    # 产生 dir 风格输出（<DIR> 行 + 文件行 + 中文统计行）
    out = b''
    for i in range(60):
        out += f"2026/08/{i % 28 + 1:02d}  {i:02d}:00    <DIR>          dir_{i:03d}\r\n".encode()
    out += b"2026/08/09  16:35             4,549 speedtest_nodes.py\r\n"
    out += b"2026/08/23  13:28    <DIR>          __rikka_kimi\r\n"
    out += b"2026/08/23  13:53    <DIR>          __rikka_pi\r\n"
    out += "              35 个文件        460,671 字节\r\n".encode()
    out += b"C:\\Users\\rikka>dir"
    t.feed(out)

    # 多次 resize：验证连续 reflow 的累积正确性（用户场景）
    seq = [22, 77, 22, 77, 22, 77, 14, 95]
    for i, cols in enumerate(seq):
        t.resize(cols, 17)
        check(f"resize#{i}_to{cols}", t, "__rikka_kimi")
        check(f"resize#{i}_to{cols}_speed", t, "speedtest_nodes.py")

if __name__ == '__main__':
    main()
