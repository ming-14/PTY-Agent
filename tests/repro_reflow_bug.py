# -*- coding: utf-8 -*-
"""最小复现：grid.py reflow 变窄→变宽内容错乱（网页端 resize bug）

复现自生产日志 daemon-20260724-185019.313.log：
- 会话 51x17 -> 13x17 -> 37x17 -> 52x17 后快照出现
  "Microsoft Windows [版本 10.0.19045.6456Microsoft Win"（内容重复）
- 另一会话 91x24 -> 10x5 后 scrollback 出现 "MicrosoftWindows"（边界空格丢失）

bug 1：_reflow_lines split-else 分支把 merged append 两次（重复 + wrap 链被劫持）
bug 2：_merge_lines 以 width()（裁掉尾部默认 cell）作为拼接偏移，
        wrap 边界恰好是空格时真实空格被吞
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.terminal.grid import Grid, GridCell, GridLine  # noqa: E402


def make_line(text, cols, wrapped=False):
    cells = [GridCell() for _ in range(cols)]
    for i, ch in enumerate(text):
        if i < cols:
            cells[i] = GridCell(data=ch)
    line = GridLine(cells=cells, flags=0)
    line.wrapped = wrapped
    return line


def dump(grid):
    out = []
    for i, line in enumerate(grid.linedata):
        text = "".join(c.data for c in line.cells).rstrip()
        out.append("  linedata[%d] w=%-5s %r" % (i, line.wrapped, text))
    return "\n".join(out)


def total_text(grid):
    """拼接全部行的可见文本（用于检测内容重复/丢失）"""
    return "".join("".join(c.data for c in line.cells).rstrip()
                   for line in grid.linedata)


def repro_bug1():
    """长链 13 -> 37：merged + next > new_cols 的 split 分支"""
    print("=== bug 1: wrap 链 13 -> 37 变宽 ===")
    # 逻辑行 "0"*40 在 13 列下拆成 13+13+13+1
    grid = Grid(cols=13, rows=6, hlimit=100)
    grid.linedata[0] = make_line("0" * 13, 13, wrapped=True)
    grid.linedata[1] = make_line("1" * 13, 13, wrapped=True)
    grid.linedata[2] = make_line("2" * 13, 13, wrapped=True)
    grid.linedata[3] = make_line("3", 13, wrapped=False)
    grid.linedata[4] = GridLine.empty(13)
    grid.linedata[5] = GridLine.empty(13)

    grid.reflow(37, 6)
    print(dump(grid))
    joined = total_text(grid)
    expect = "0" * 13 + "1" * 13 + "2" * 13 + "3"
    print("拼接文本:", joined)
    print("期望文本:", expect)
    ok = joined == expect
    print("BUG1", "未复现（已修复）" if ok else "复现：内容重复/错乱")
    return ok


def repro_bug2():
    """wrap 边界为空格：10 -> 17 合并后空格被吞"""
    print("\n=== bug 2: 'Microsoft ' 边界空格 ===")
    # "Microsoft Windows" 在 10 列下拆成 "Microsoft "(W) + "Windows"
    grid = Grid(cols=10, rows=3, hlimit=100)
    grid.linedata[0] = make_line("Microsoft ", 10, wrapped=True)
    grid.linedata[1] = make_line("Windows", 10, wrapped=False)
    grid.linedata[2] = GridLine.empty(10)

    grid.reflow(17, 3)
    print(dump(grid))
    text = "".join(c.data for c in grid.linedata[0].cells).rstrip()
    print("合并后第一行: %r" % text)
    ok = text == "Microsoft Windows"
    print("BUG2", "未复现（已修复）" if ok else "复现：边界空格丢失 -> %r" % text)
    return ok


def repro_roundtrip():
    """真实场景回归：51 -> 13 -> 37 -> 52 内容应保持不变"""
    print("\n=== 场景回归: 51 -> 13 -> 37 -> 52 往返 ===")
    banner = "Microsoft Windows [ver 10.0.19045.6456]"  # 40 字符
    cpline = "(c) Microsoft Corporation. All rights."
    prompt = "C:\\Users\\rikka\\Desktop\\PTY-Agent>"
    grid = Grid(cols=51, rows=17, hlimit=100)
    grid.linedata[0] = make_line(banner, 51)
    grid.linedata[1] = make_line(cpline, 51)
    grid.linedata[2] = GridLine.empty(51)
    grid.linedata[3] = make_line(prompt, 51)

    grid.reflow(13, 17)
    grid.reflow(37, 17)
    grid.reflow(52, 17)
    print(dump(grid))
    texts = ["".join(c.data for c in grid.linedata[i].cells).rstrip()
             for i in range(4)]
    ok = (texts[0] == banner and texts[1] == cpline
          and texts[2] == "" and texts[3] == prompt)
    print("前4行:", texts)
    print("ROUNDTRIP", "正常" if ok else "错乱")
    return ok


if __name__ == "__main__":
    r1 = repro_bug1()
    r2 = repro_bug2()
    r3 = repro_roundtrip()
    print("\n汇总: bug1_ok=%s bug2_ok=%s roundtrip_ok=%s" % (r1, r2, r3))
    sys.exit(0 if (r1 and r2 and r3) else 1)
