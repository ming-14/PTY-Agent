"""组合根：依赖组装、线程编排、CLI 入口。

本文件是架构最外层，负责把驱动/适配/用例组装成可运行程序：
- 建一个共享 MuxPanel（两个 pane + 分隔线 + 状态栏，渲染/路由统一交给 Mux）；
- 持有共享状态 dict+lock、创建渲染线程、驱动主事件循环。
"""

import argparse
import logging
import sys
import threading

from leaf.domain import ansi
from leaf.drivers.console import Console
from leaf.drivers.pane import MuxPanel
from leaf.usecases.frame import RenderState, render_loop
from leaf.usecases.input import handle_events
from leaf.usecases.launch import resolve_program
from leaf.adapters.clipboard import Clipboard
from leaf.adapters.output import StdoutSink

log = logging.getLogger("leaf")


def setup_logging() -> None:
    """日志写文件，控制台仅 WARNING：TUI 全屏重绘下 stderr 日志会混入画面。"""
    root = logging.getLogger("leaf")
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    fh = logging.FileHandler("leaf.log", encoding="utf-8", delay=True)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="leaf 分屏 demo（Mux 复用器）")
    ap.add_argument("--pane1", nargs="+", default=["cmd"])
    ap.add_argument("--pane2", nargs="+", required=True)
    args = ap.parse_args(argv)

    setup_logging()

    # 非 Windows 无 ConPTY 语义，直接拒绝运行
    if sys.platform != "win32":
        print("leaf 依赖 Windows ConPTY，仅在 Windows 上运行", file=sys.stderr)
        return 1

    console = Console()
    cols, rows = console.size()
    log.info("host terminal %sx%s", cols, rows)

    render_event = threading.Event()
    mux = MuxPanel(cols, rows)
    mux.add_pane(resolve_program(args.pane1, "cmd.exe"), name="cmd")
    mux.add_pane(resolve_program(args.pane2, "hx.exe"), name="hx")
    mux.set_focus(0)  # 初始焦点固定左 pane（Rust 侧 add_pane 后焦点是最后一个 pane）
    lock = threading.Lock()
    split_col = mux.split_col()  # 分割线初始列（= 左 pane 宽）
    state: RenderState = {
        "cols": cols, "rows": rows, "focus": 0, "render_event": render_event,
    }
    stop_event = threading.Event()
    output = StdoutSink()
    clipboard = Clipboard()
    drag = None       # 分割线拖拽状态：(起始鼠标 x, 起始 split_col)
    last_move = 0.0   # move 转发节流时间戳
    sel_drag = None   # 文本选区拖拽状态：(pane_id, 锚点 x, 锚点 y)

    # 应用 OSC 52 写剪贴板 → 系统剪贴板（回调只写剪贴板，不反查终端）
    mux.set_focus_selection_callback(clipboard.write)

    try:
        # 启动清场：清屏 + 清 scrollback（省掉宿主 shell 历史产生滚动条）
        output.write(ansi.ANSI_HIDE_CURSOR + "\x1b[2J\x1b[3J\x1b[H")
        output.flush()
        render_thread = threading.Thread(
            target=render_loop, args=(mux, output, state, lock, stop_event),
            name="render", daemon=True,
        )
        render_thread.start()
        while True:
            # 输入事件阻塞等待（输入键到达即处理，无轮询）
            if console.wait_input(16):
                with lock:
                    focus = state["focus"]
                    (focus, exit_, cols, rows, _, split_col, drag, last_move,
                     force_full, sel_drag) = handle_events(
                        mux, console, focus, cols, rows, split_col, drag, last_move,
                        sel_drag, clipboard
                    )
                    state["focus"] = focus
                    state["cols"] = cols
                    state["rows"] = rows
                    if force_full:
                        state["force_full"] = True
                render_event.set()  # 输入/拖拽后立即重绘
                if exit_:
                    break
            if mux.all_eof():
                log.info("both panes eof, exit")
                break
    finally:
        stop_event.set()
        render_event.set()
        render_thread.join(timeout=2.0)
        output.write(ansi.ANSI_RESET + ansi.ANSI_SHOW_CURSOR + "\x1b[2J\x1b[H")
        output.flush()
        console.restore()
        mux.close()
    return 0