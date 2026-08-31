"""组合根：依赖组装、线程编排、CLI 入口。

本文件是架构最外层，负责把驱动/适配/用例组装成可运行程序：
- 建一个共享 MuxPanel（两个 pane + 分隔线 + 状态栏，渲染/路由统一交给 Mux）；
- 持有共享状态 dict+lock、创建渲染线程、驱动主事件循环；
- asciinema 子命令：play/cat/convert；
- 分屏模式下支持 --record 录制。
"""

import argparse
import ctypes
import logging
import os
import re
import sys
import threading
import time

from leaf.domain import ansi
from leaf.domain.asciicast import Header
from leaf.drivers.console import Console
from leaf.drivers.pane import MuxPanel
from leaf.usecases.frame import RenderState, render_loop
from leaf.usecases.input import handle_events
from leaf.usecases.launch import resolve_program
from leaf.usecases.recorder import Recorder
from leaf.usecases.ports import RecorderSlot
from leaf.adapters.clipboard import Clipboard
from leaf.adapters.output import StdoutSink, NullSink
from leaf.adapters.castfile import CastFileWriter, open_from_path

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


def _require_windows() -> int:
    if sys.platform != "win32":
        print("leaf 依赖 Windows ConPTY，仅在 Windows 上运行", file=sys.stderr)
        return 1
    return 0


# ---- 分屏主模式（可录制） ----

def _run_split_screen(argv) -> int:
    ap = argparse.ArgumentParser(description="leaf 分屏终端（Mux 复用器）")
    ap.add_argument("--pane1", nargs="+", default=["cmd"])
    ap.add_argument("--pane2", nargs="+", default=None,
                    help="右窗格程序（默认不开启右窗格，仅左 pane 单窗格模式）")
    # 录制选项
    ap.add_argument("--record", "-r", help="录制到文件")
    ap.add_argument("--capture-input", action="store_true", help="录制键盘输入")
    ap.add_argument("--title", help="录制标题")
    ap.add_argument("--idle-time-limit", type=float, help="空闲时间限制（秒）")
    ap.add_argument("--append", action="store_true", help="追加到现有文件")
    ap.add_argument("--overwrite", action="store_true", help="覆盖现有文件")
    ap.add_argument("--headless", action="store_true", help="无头模式（不显示）")
    args = ap.parse_args(argv)

    setup_logging()
    if _require_windows():
        return 1

    console = Console()
    cols, rows = console.size()
    log.info("host terminal %sx%s", cols, rows)

    # 录制器动态切换（F8 开始/结束，--record 启动时自动开始）
    recorder_slot = RecorderSlot()
    if args.record:
        header = Header(
            cols=cols, rows=rows,
            title=args.title,
            idle_time_limit=args.idle_time_limit,
            command=" ".join(argv),
        )
        writer = CastFileWriter(args.record, header,
                                append=args.append, overwrite=args.overwrite)
        recorder_slot.set(Recorder(writer))
        recorder_slot.get().resize(cols, rows)

    def toggle_recording():
        """F8 开始/结束录制：文件 = 工作目录/窗口标题.cast"""
        current = recorder_slot.get()
        if current is not None:
            log.info("recording stopped")
            current.finish()
            recorder_slot.set(None)
        else:
            # 获取窗口标题，清理非法文件名字符
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.windll.kernel32.GetConsoleTitleW(buf, 1024)
            title = buf.value if n else ""
            if not title:
                title = "leaf"
            safe = re.sub(r'[\\/:*?"<>|]', "_", title.strip())
            path = os.path.join(os.getcwd(), f"{safe}.cast")
            log.info("recording started → %s", path)
            h = Header(cols=cols, rows=rows,
                       title=safe, idle_time_limit=args.idle_time_limit,
                       command=" ".join(argv))
            w = CastFileWriter(path, h, overwrite=False)
            rec = Recorder(w)
            rec.resize(cols, rows)
            recorder_slot.set(rec)
            # 同步强制渲染当前画面并录制：不等渲染线程（可能被 MIN_FRAME 或
            # 无变化帧跳过），确保 F8 时刻的屏幕内容立即进入录制文件
            mux.force_repaint()
            data, cr, cc, cv = mux.render()
            cursor_seq = f"\x1b[{cr + 1};{cc + 1}H\x1b[{'?25h' if cv else '?25l'}"
            combined = (bytes(data) if data else b"") + cursor_seq.encode("utf-8")
            if combined:
                rec.output(combined)

    render_event = threading.Event()
    mux = MuxPanel(cols, rows)
    # 新输出回调驱动渲染（reader 线程 feed 后 set event，替代定时轮询）
    mux.set_output_callback(render_event.set)
    mux.add_pane(resolve_program(args.pane1, "cmd.exe"), name="cmd")
    has_pane2 = args.pane2 is not None
    if has_pane2:
        mux.add_pane(resolve_program(args.pane2, "hx.exe"), name="hx")
    mux.set_focus(0)
    lock = threading.Lock()
    split_col = mux.split_col()
    state: RenderState = {
        "cols": cols, "rows": rows, "focus": 0, "render_event": render_event,
    }
    stop_event = threading.Event()
    output = StdoutSink()
    clipboard = Clipboard()
    drag = None
    last_move = 0.0
    sel_drag = None

    mux.set_focus_selection_callback(clipboard.write)

    try:
        if not args.headless:
            output.write(ansi.ANSI_HIDE_CURSOR + "\x1b[2J\x1b[3J\x1b[H")
            output.flush()
        # 渲染线程总是启动（headless 时输出到 NullSink，但录制仍依赖 render 输出）
        sink = output if not args.headless else NullSink()
        render_thread = threading.Thread(
            target=render_loop,
            args=(mux, sink, state, lock, stop_event, recorder_slot),
            name="render", daemon=True,
        )
        render_thread.start()
        render_event.set()  # 立即渲染首帧（录制需要完整首帧基线）

        while True:
            if not args.headless and console.wait_input(16):
                with lock:
                    focus = state["focus"]
                    (focus, exit_, cols, rows, _, split_col, drag, last_move,
                     force_full, sel_drag) = handle_events(
                        mux, console, focus, cols, rows, split_col, drag, last_move,
                        sel_drag, clipboard,
                        recorder=(recorder_slot.get() if args.capture_input else None),
                        toggle_recording=toggle_recording,
                    )
                    state["focus"] = focus
                    state["cols"] = cols
                    state["rows"] = rows
                    if force_full:
                        state["force_full"] = True
                render_event.set()
                if exit_:
                    break
            elif args.headless:
                # 无头模式：无输入，仅等待子进程退出（渲染由 Mux 新输出回调
                # + RENDER_POLL 兜底驱动，无需手动 set）
                if mux.all_eof():
                    break
                time.sleep(0.01)
            if mux.all_eof():
                log.info("both panes eof, exit")
                recorder = recorder_slot.get()
                if recorder is not None:
                    # 等 reader 线程把 EOF 前的残余输出 drain 进终端，再做最终渲染
                    render_event.set()
                    time.sleep(0.15)
                    # 显式最终渲染：避免 render 线程未及时消费导致末帧丢失
                    data, cr, cc, cv = mux.render()
                    if data:
                        recorder.output(bytes(data))
                    recorder.output(mux.cursor_seq(cr, cc, cv).encode("utf-8"))
                    code = 0
                    for i in range(mux.pane_count()):
                        code = max(code, mux.pane_exit_code(i))
                    recorder.exit(code)
                break
    finally:
        recorder = recorder_slot.get()
        if recorder is not None:
            recorder.finish()
        stop_event.set()
        render_event.set()
        if render_thread is not None:
            render_thread.join(timeout=2.0)
        if not args.headless:
            output.write(ansi.ANSI_RESET + ansi.ANSI_SHOW_CURSOR + "\x1b[2J\x1b[H")
            output.flush()
        console.restore()
        mux.close()
    return 0


# ---- 回放 ----

def _run_play(argv) -> int:
    ap = argparse.ArgumentParser(description="leaf play — 回放 asciicast 录制")
    ap.add_argument("file", help="录制文件（本地路径或 http(s) URL）")
    ap.add_argument("--speed", "-s", type=float, default=1.0, help="播放速度")
    ap.add_argument("--loop", "-l", action="store_true", help="循环播放")
    ap.add_argument("--idle-time-limit", type=float, help="空闲时间限制（秒）")
    ap.add_argument("--pause-on-markers", "-m", action="store_true", help="遇标记暂停")
    ap.add_argument("--resize", "-r", action="store_true", help="自动调整终端尺寸")
    args = ap.parse_args(argv)

    setup_logging()
    if _require_windows():
        return 1

    from leaf.drivers import _engine
    _engine.ensure_engine()
    import pywezterm
    from leaf.usecases.player import Player

    console = Console()
    output = StdoutSink()
    header, _version, _events = open_from_path(args.file)
    term = pywezterm.Terminal(header.cols, header.rows, scrollback=10000)
    player = Player(term, output, console)
    try:
        finished = player.play(
            args.file, speed=args.speed,
            idle_time_limit=args.idle_time_limit,
            pause_on_markers=args.pause_on_markers,
            loop=args.loop,
            auto_resize=args.resize,
        )
    finally:
        console.restore()
        output.write("\x1b[?25h\x1b[0m")
        output.flush()
    return 0 if finished else 1


# ---- cat / convert ----

def _run_cat(argv) -> int:
    ap = argparse.ArgumentParser(description="leaf cat — 拼接多个 asciicast 录制")
    ap.add_argument("files", nargs="+", help="录制文件（至少 2 个）")
    ap.add_argument("-o", "--output", default="-", help="输出路径（默认 stdout）")
    ap.add_argument("-f", "--output-format", choices=["v3"], help="输出版本")
    args = ap.parse_args(argv)

    if len(args.files) < 2:
        print("cat 需要至少 2 个输入文件", file=sys.stderr)
        return 1

    from leaf.usecases.cast_ops import cat
    try:
        cat(args.files, args.output, args.output_format)
    except Exception as e:
        print(f"cat 失败: {e}", file=sys.stderr)
        return 1
    return 0


def _run_convert(argv) -> int:
    ap = argparse.ArgumentParser(description="leaf convert — 转换 asciicast 格式")
    ap.add_argument("input", help="输入文件")
    ap.add_argument("output", help="输出文件（- = stdout）")
    ap.add_argument("--output-format", "-f",
                    choices=["v3", "raw", "txt", "mp4"],
                    default="v3", help="输出格式")
    ap.add_argument("--overwrite", action="store_true", help="覆盖输出文件")
    ap.add_argument("--cell-size", type=int, default=8,
                    help="mp4 每格像素宽（默认 8，格高=宽×2）")
    ap.add_argument("--tail", type=float, default=1.0,
                    help="mp4 末帧保持秒数（默认 1.0）")
    ap.add_argument("--padding", type=int, default=14,
                    help="mp4 四周边框像素（默认 14，0=无边框）")
    ap.add_argument("--border-color", type=str, default="12,12,12",
                    help="mp4 边框颜色，R,G,B 或 RRGGBB（默认 12,12,12）")
    args = ap.parse_args(argv)

    if args.output_format == "mp4":
        from leaf.adapters.mp4_export import export_mp4
        try:
            export_mp4(args.input, args.output,
                       cell_w=args.cell_size, overwrite=args.overwrite,
                       tail=args.tail, padding=args.padding,
                       border_color=args.border_color)
        except Exception as e:
            print(f"convert 失败: {e}", file=sys.stderr)
            return 1
        return 0

    from leaf.usecases.cast_ops import convert
    try:
        convert(args.input, args.output, args.output_format, overwrite=args.overwrite)
    except Exception as e:
        print(f"convert 失败: {e}", file=sys.stderr)
        return 1
    return 0


# ---- 主入口 ----

def main(argv) -> int:
    if not argv:
        print("Usage: leaf [--pane1 ...] [--pane2 ...] [options]")
        print("   or: leaf rec <file> [options]")
        print("   or: leaf play <file> [options]")
        print("   or: leaf cat <files...> [options]")
        print("   or: leaf convert <input> <output> [options]")
        print("   or: leaf session <file> [options]")
        return 1

    cmd = argv[0]
    if cmd == "play":
        return _run_play(argv[1:])
    elif cmd == "cat":
        return _run_cat(argv[1:])
    elif cmd == "convert":
        return _run_convert(argv[1:])
    elif cmd == "session":
        if len(argv) < 2:
            print("session 需要指定输出文件路径", file=sys.stderr)
            return 1
        return _run_split_screen(["--record", argv[1]] + argv[2:])
    elif cmd == "rec":
        if len(argv) < 2:
            print("rec 需要指定输出文件路径", file=sys.stderr)
            return 1
        return _run_split_screen(["--record", argv[1]] + argv[2:])
    else:
        return _run_split_screen(argv)