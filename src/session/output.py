"""Session 输出混入 — OutputMixin

负责会话的输出读取与终端屏幕快照：增量输出、编码探测、快照/差异/scrollback、
resize 同步、终端状态查询。输入写入见 io.py。
所有方法均通过 Session 实例访问子组件（见 session.py 的 __init__）。
"""

from ..logging import get_logger
import time
from typing import List, Optional

_logger = get_logger("pty-session")


class OutputMixin:
    """输出读取与终端屏幕快照（会话组合的输出部分）"""

    def detect_encoding(self, sample: Optional[bytes] = None) -> Optional[str]:
        """基于已有输出锁定编码，供 WebSocket 等外部订阅者使用"""
        data = sample
        if data is None:
            data = self._out_buf.get_slice(max(0, self._out_buf.length - 4096))
        if data:
            self._enc.detect_decode(data)
        return self.encoding

    def get_output(
        self,
        from_offset: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> str:
        """获取会话输出（from_offset 为绝对流偏移，默认从保留起点）

        使用流偏移读取，头裁剪不会使请求位置漂移（落后于裁剪点则从保留起点取）。
        """
        data, _actual, _drop = self._out_buf.read_stream(
            from_offset if from_offset is not None else 0
        )
        return self._enc.detect_decode(data, encoding)

    def get_output_with_offset(
        self,
        from_offset: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> tuple:
        """原子获取会话输出及当前流末尾（消除 TOCTOU 竞态）

        from_offset 为绝对流偏移（默认从保留起点）。返回的是解码后文本与
        单调流末尾（stream_end），供响应 outputOffset / 游标推进使用。

        Returns:
            (解码后文本, 当前流末尾绝对偏移) 元组。
        """
        data, _actual, _drop = self._out_buf.read_stream(
            from_offset if from_offset is not None else 0
        )
        return self._enc.detect_decode(data, encoding), self._out_buf.stream_end

    def get_snapshot(self, keep_ansi: bool = False) -> str:
        """获取终端屏幕快照（经插件 on_snapshot 变换链）

        子进程模式无终端，返回空串。
        """
        if self._screen is None:
            return ""
        text = self._screen.snapshot(keep_ansi=keep_ansi)
        return self.plugin_host.on_snapshot(text)

    def get_full_snapshot(self, keep_ansi: bool = False) -> str:
        """获取全量内容：scrollback 历史 + 当前可见区快照（--full）

        scrollback 与可见区按 keep_ansi 渲染后拼接；无 scrollback 时等同 get_snapshot。

        子进程模式无终端，返回空串。
        """
        if self._screen is None:
            return ""
        sb = self._screen.capture_scrollback(keep_ansi=keep_ansi)
        snap = self._screen.snapshot(keep_ansi=keep_ansi)
        if not sb:
            return self.plugin_host.on_snapshot(snap)
        if keep_ansi:
            # scrollback 行以 \r\n 结尾，snapshot 整屏（render_ansi 每行前 CUP 定位）直接拼接
            text = sb + snap
        else:
            # 纯文本：scrollback 行间 \n（无尾 \n），补一个换行接可见区
            text = sb + "\n" + snap
        return self.plugin_host.on_snapshot(text)

    def get_cursor_seq(self) -> str:
        """获取光标定位 VT 序列（CSI row;col H + ?25h/l）

        v6 fix: 供 web 层订阅时附加到 replay 末尾，
        确保前端 replayPending 写入 replay 后光标定位到 PTY 真实位置。
        """
        return self._screen.get_cursor_seq()

    def capture_scrollback(self) -> str:
        """捕获 scrollback 历史区为 ANSI 字符串（带 SGR 颜色）

        供 web 层 subscribe 响应返回给前端，
        前端写入 xterm.js 推入 scrollback 区，实现 F5 刷新/重开浏览器后 scrollback 不丢。

        Returns:
            每行 ANSI 内容 + \\r\\n 的字符串；无 scrollback 时返回 ""。
        """
        return self._screen.capture_scrollback()

    def clear_scrollback(self) -> None:
        """清除 Grid scrollback 历史区

        resize 后 ConPTY repaint 可能触发 index() 将可见区顶部行推入
        scrollback，导致 scrollback 与 snapshot 内容重叠。
        """
        self._screen.clear_scrollback()

    def get_snapshot_diff(self, keep_ansi: bool = False) -> str:
        """获取终端屏幕快照中与上次相比变化的行

        内容仅能经 feed（feed_count）或 resize（cols/rows）改变：
        两者均未变化时直接返回空，避免全量重渲染 + 行对比。
        """
        key = (
            self._screen.feed_count,
            self._screen.cols,
            self._screen.rows,
            keep_ansi,
        )
        if key == self._last_snapshot_key:
            return ""
        self._last_snapshot_key = key

        current_text = self._screen.snapshot(keep_ansi=keep_ansi)
        current_lines = current_text.split("\n") if current_text else []
        if self._last_snapshot_lines is None:
            self._last_snapshot_lines = current_lines
            return current_text
        diff_lines = []
        max_len = max(len(current_lines), len(self._last_snapshot_lines))
        for i in range(max_len):
            cur = current_lines[i] if i < len(current_lines) else ""
            prev = (
                self._last_snapshot_lines[i]
                if i < len(self._last_snapshot_lines)
                else ""
            )
            if cur != prev:
                diff_lines.append(f"{i}:{cur}")
        self._last_snapshot_lines = current_lines
        return "\n".join(diff_lines)

    def get_snapshot_diagnostics(self) -> dict:
        return self._screen.diagnostics()

    def export_screen_buffer(self) -> dict:
        """导出字符网格为可序列化字典（子进程模式无终端，返回空 dict）"""
        if self._screen is None:
            return {}
        return self._screen.export_buffer()

    def resize(self, cols: int, rows: int) -> tuple:
        """调整终端尺寸（PTY + wezterm screen + InputInterceptor）

        子进程模式无终端，调用即报错。

        v5 方案（对齐 ConPTY 语义）+ scrollback 保留：
        - 先 resize wezterm screen（wezterm-term 原生 reflow：内容锚顶、
          光标绑定文本行，与 ConPTY 坐标系完全一致）
        - 立即捕获 scrollback（此刻的 scrollback 是纯 reflow 历史，
          不含后续 repaint 推入的可见区行 → 与 snapshot 天然无重叠）
        - 再 resize PTY（ConPTY 内部 reflow；宽度变化时会发 repaint）
        - 短暂等待 ConPTY repaint（如果有的话），让终端模型同步到最新状态
        - 清除终端模型 scrollback（仅清 repaint 竞态推入的冗余行，
          保证后续订阅/--full 捕获不重叠；返回的 scrollback 是第 2 步副本）
        - 返回 (snapshot, scrollback)：snapshot 含 VT 颜色序列 + 真实光标位置，
          scrollback 为 reflow 历史（每行 ANSI + \\r\\n）
        - 前端收到后 \\x1b[3J + scrollback + \\x1b[2J + snapshot 重建

        关键不变量：snapshot 的可见区内容和光标 == ConPTY 的可见区内容和光标。
        违背此不变量会导致 resize 后 ConPTY 的绝对光标定位（\\x1b[row;colH）
        落在前端显示内容的中间 —— "光标在 dir 输出中间" bug（历史根因：
        旧 Grid.reflow 锚底 reflow 把 scrollback 行提升进可见区，见
        tests/e2e/test_resize_cursor_sync.py 的实证注释）。

        Returns:
            (snapshot, scrollback) 元组：snapshot 为屏幕快照（含 VT 颜色序列
            与光标位置），scrollback 为 reflow 历史（ANSI + \\r\\n，无历史为 ""），
            供前端重建 buffer 使用。
        """
        if getattr(self, "mode", "pty") == "subprocess":
            raise RuntimeError("子进程模式不支持 resize（无终端）")

        cols, rows = int(cols), int(rows)
        _logger.debug(
            "resize: START %dx%d -> %dx%d", self._cols, self._rows, cols, rows
        )

        # resize 前 cursor 位置（诊断用）
        try:
            old_cursor = self._screen.cursor_position()
            _logger.debug(
                "resize: before screen.resize cursor=(x=%s y=%s)",
                old_cursor[0],
                old_cursor[1],
            )
        except Exception:
            pass

        # 1. 先 resize 终端模型（reflow 内容 + 保留光标）
        #    必须在 pty.resize() 之前完成，这样 reader 线程后续读到的 repaint
        #    字节会以新尺寸被终端模型正确处理，避免"内容错位"竞态
        screen_ok = True
        try:
            self._screen.resize(cols, rows)
        except Exception as e:
            _logger.warning("resize screen failed: %s", e)
            screen_ok = False

        if screen_ok:
            self._cols, self._rows = cols, rows
            self._input_interceptor.resize(cols, rows)

        # 1.5 捕获 scrollback（纯 reflow 历史）
        #    pty.resize() 之前捕获：此时模型 scrollback 只有 reflow 产生的真实
        #    历史，不含 repaint 竞态推入的可见区行，与最终 snapshot 天然无重叠。
        #    前端 restoreScrollbackAndSnapshot 写 scrollback 后接 snapshot 不会重复。
        scrollback_ansi = ""
        if screen_ok:
            try:
                scrollback_ansi = self._screen.capture_scrollback(keep_ansi=True)
                _logger.debug(
                    "resize: captured scrollback len=%d head=%r",
                    len(scrollback_ansi),
                    scrollback_ansi[:80].replace("\r", "\\r").replace("\x1b", "\\e"),
                )
            except Exception as e:
                _logger.warning("resize: capture scrollback failed (non-fatal): %s", e)

        # 2. resize PTY（ConPTY 内部 reflow + 发送 repaint）
        #    让 ConPTY repaint 直达前端
        pty_ok = True
        try:
            if self._pty and hasattr(self._pty, "resize"):
                self._pty.resize(cols, rows)
        except Exception as e:
            _logger.warning("resize pty failed: %s", e)
            pty_ok = False

        if not (pty_ok and screen_ok):
            _logger.warning(
                "resize partial (pty_ok=%s, screen_ok=%s), size=%dx%d",
                pty_ok,
                screen_ok,
                self._cols,
                self._rows,
            )

        # 3. 短暂等待 ConPTY repaint（如果有的话）
        #    终端模型已有 reflow 后的旧内容，即使 ConPTY 不发 repaint，
        #    快照仍然包含正确的内容和光标位置
        if pty_ok:
            prior_feed = self._screen.feed_count
            _logger.debug(
                "resize: waiting for optional repaint feed, prior_feed_count=%d",
                prior_feed,
            )
            # 最多等 200ms 让 reader feed repaint
            waited_ms = 0
            for _ in range(20):
                if self._screen.feed_count > prior_feed:
                    break
                time.sleep(0.01)
                waited_ms += 10
            # 若收到 repaint，再等 60ms 让字节稳定
            if self._screen.feed_count > prior_feed:
                stable_ms = 0
                last_count = self._screen.feed_count
                for _ in range(10):
                    time.sleep(0.03)
                    cur_count = self._screen.feed_count
                    if cur_count == last_count:
                        stable_ms += 30
                        if stable_ms >= 60:
                            break
                    else:
                        stable_ms = 0
                        last_count = cur_count
            _logger.debug(
                "resize: waited %dms, feed_count %d→%d (Δ=%d)",
                waited_ms,
                prior_feed,
                self._screen.feed_count,
                self._screen.feed_count - prior_feed,
            )

        # snapshot 前 cursor 位置（诊断用）
        try:
            new_cursor = self._screen.cursor_position()
            _logger.debug(
                "resize: before snapshot cursor=(x=%s y=%s) hidden=%s",
                new_cursor[0],
                new_cursor[1],
                (not new_cursor[2]) if new_cursor[2] is not None else "?",
            )
        except Exception:
            pass

        _logger.debug("resize: END, returning snapshot")
        try:
            # 清除终端模型 scrollback：resize 后 ConPTY repaint 可能触发 index()
            # 将可见区顶部行推入模型 scrollback，导致模型 scrollback 与 snapshot
            # （读终端模型可见区）内容重叠 —— 若不清除，后续订阅/--full 的
            # capture_scrollback 会包含这些冗余行。
            # 本 resize 返回的 scrollback 是第 1.5 步捕获的干净副本，不受影响；
            # 模型 scrollback 清除后由后续正常输出滚动重新产生。
            try:
                self._screen.clear_scrollback()
                _logger.debug("resize: cleared model scrollback after repaint wait")
            except Exception as e:
                _logger.debug("resize: clear_scrollback failed (non-fatal): %s", e)

            # 关键：snapshot 必须来自终端模型（ConPTY 真实可见区状态）。
            # wezterm 终端模型 resize 已按 ConPTY 语义原生 reflow，
            # 此处读出的内容和光标与 ConPTY 坐标系完全一致，
            # 前端重建后 ConPTY 的绝对光标定位不会错位。
            snapshot = self._screen.snapshot(keep_ansi=True, include_cursor=True)
            # 诊断日志：快照前 100 字符 + 末尾 60 字符
            preview_head = (
                snapshot[:100]
                .replace("\r", "\\r")
                .replace("\n", "\\n")
                .replace("\x1b", "\\e")
            )
            preview_tail = (
                snapshot[-60:]
                .replace("\r", "\\r")
                .replace("\n", "\\n")
                .replace("\x1b", "\\e")
            )
            _logger.debug(
                "resize: snapshot len=%d head=%r tail=%r",
                len(snapshot),
                preview_head,
                preview_tail,
            )
            _logger.debug(
                "resize: returning scrollback len=%d (preserved)",
                len(scrollback_ansi),
            )
            return (snapshot, scrollback_ansi)
        except Exception as e:
            _logger.warning("resize: 返回 snapshot 失败: %s", e)
            return ("", "")

    def _apply_program_resize(self, cols: int, rows: int) -> None:
        """应用程序发起的尺寸变更（CSI 8;rows;colst）并广播

        wezterm 终端模型忽略窗口操作序列，程序请求的尺寸需在此落到
        PTY/屏幕，并经 publisher 通知（web 端据此立即响应）。
        """
        cols = max(1, min(int(cols), 1000))
        rows = max(1, min(int(rows), 1000))
        if (cols, rows) == (self._cols, self._rows):
            return
        _logger.info("会话 '%s': 应用程序 resize -> %dx%d", self.id, cols, rows)
        try:
            snapshot, scrollback_ansi = self.resize(cols, rows)
        except Exception as e:
            _logger.warning("会话 '%s': 程序 resize 失败: %s", self.id, e)
            return
        try:
            self._publisher.notify_resized(
                self, cols, rows, snapshot, scrollback_ansi
            )
        except Exception as e:
            _logger.warning("会话 '%s': 广播程序 resize 失败: %s", self.id, e)

    def cursor_position(self):
        """获取终端当前光标位置与可见性

        Returns:
            (x, y, visible) 元组；x/y 为 0-based 列/行，
            visible 为 None 表示可见性未知。任意线程可调用。
        """
        return self._screen.cursor_position()

    def is_alt_screen(self) -> bool:
        """备用屏幕是否激活（vim/htop/less 等 TUI 应用）

        基于终端层对 \\x1b[?1049/1047/47/1048 开关序列的跟踪，任意线程可调用。
        """
        return self._screen.is_alt_screen()

    def mode_restore_seq(self) -> str:
        """终端模式恢复序列（订阅时拼在 replay 前，恢复 xterm 模式状态）

        鼠标追踪/光标可见性/bracketed paste/备用屏幕等，见 TerminalScreen.mode_restore_seq。
        """
        return self._screen.mode_restore_seq()

    def is_mouse_tracking(self) -> bool:
        """TUI 应用是否激活鼠标追踪（\\x1b[?1000/1002/1003 跟踪）

        供 web 订阅响应携带当前鼠标模式，前端据此恢复鼠标输入状态。
        """
        return self._screen.is_mouse_tracking()
