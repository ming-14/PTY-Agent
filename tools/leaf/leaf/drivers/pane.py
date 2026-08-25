"""窗格驱动：一个共享 Mux 复用器的门面（MuxPanel）与单窗格薄封装（Pane）。

leaf 不再自建 Pty/Terminal/reader/差分渲染——全部交给 pywezterm.Mux：
- 门面持有**一个** Mux（两个 pane + 分隔线 + 底部状态栏），渲染/事件路由/滚动
  统一由 Mux 合成（增量字节 + 整屏坐标光标）；
- Pane 是单 pane Mux 的薄封装（供 test_e2e/独立场景复用）。

本文件与 console.py 是仅有的两个加载 pywezterm 的驱动模块（共用 _engine
注入 vendored 产物路径）；用例层仅依赖门面协议。
"""

import logging
import os

from leaf.drivers import _engine

_engine.ensure_engine()

import pywezterm

log = logging.getLogger("leaf.pane")

# 滚轮宿主代滚步长（行）
WHEEL_SCROLL_LINES = 3


class MuxPanel:
    """共享 Mux 门面：两个 pane + 分隔线 + 状态栏，统一合成渲染/事件路由。

    事件（键盘/鼠标/滚动）经此下发；渲染调 render() 取增量字节 + 整屏光标。
    """

    def __init__(self, cols: int, rows: int):
        self.mux = pywezterm.Mux(cols, rows)
        self.mux.set_sep(True)       # 左右 pane 之间预留一列分隔线（leaf 形态）
        self.mux.set_status_rows(1)  # 底部预留一行状态栏
        self._names = []             # pane 显示名（状态栏用）

    # ---- pane 管理与退出检测 ----
    def add_pane(self, argv, cwd=None, env=None, name=None):
        """spawn 一个子进程 pane，返回 pane id"""
        pid = self.mux.add_pane(argv, cwd, env)
        self._names.append(name or (os.path.basename(argv[0]) if argv else "?"))
        return pid

    def name(self, pane_id: int) -> str:
        """pane 显示名"""
        return self._names[pane_id] if pane_id < len(self._names) else "?"

    def pane_count(self) -> int:
        return self.mux.pane_count()

    def all_eof(self) -> bool:
        """全部子进程已退出（用于应用退出判定）"""
        n = self.mux.pane_count()
        if n == 0:
            return False
        return all(self.mux.pane_try_wait(i) is not None for i in range(n))

    # ---- 焦点 / 布局 ----
    def focused(self) -> int:
        return self.mux.focused()

    def set_focus(self, pane_id: int) -> None:
        self.mux.set_focus(pane_id)

    def split_col(self) -> int:
        """当前分隔线所在列（= 左 pane 宽度）"""
        rects = self.mux.pane_rects()
        return rects[0][2] if rects else 0

    def set_split_col(self, split_col, cols: int = 0, rows: int = 0) -> None:
        """拖拽预览：更新分割线位置（重算矩形并标记 pane 重写，不实时 resize ConPTY）"""
        self.mux.set_split_col(split_col)

    def resize(self, cols: int, rows: int) -> None:
        """宿主屏尺寸变化：重算矩形 + resize 各 pane 的 pty+终端 + 重置合成基线"""
        self.mux.resize(cols, rows)

    def pane_sizes(self):
        """各 pane 尺寸 [(cols, rows), ...]（状态栏显示用）"""
        return [(r[2], r[3]) for r in self.mux.pane_rects()]

    # ---- 事件路由 ----
    def key_down(self, key: str, mods: int) -> bytes:
        """按键编码并写入焦点 pane，返回编码字节"""
        return bytes(self.mux.key_down(key, mods))

    def key_up(self, key: str, mods: int) -> bytes:
        """抬键编码并写入焦点 pane，返回编码字节"""
        return bytes(self.mux.key_up(key, mods))

    def mouse(self, x: int, y: int, kind: str, button: str, mods: int) -> bytes:
        """整屏坐标鼠标事件：命中 pane → 坐标换算 → 编码写入，返回编码字节"""
        return bytes(self.mux.mouse(x, y, kind, button, mods))

    def pane_at(self, x: int, y: int):
        """整屏坐标命中的 pane id（None = 分隔线/状态栏/未命中）"""
        return self.mux.pane_at(x, y)

    def pane_is_mouse_grabbed(self, pane_id: int) -> bool:
        return bool(self.mux.pane_is_mouse_grabbed(pane_id))

    def scroll_focused(self, delta: int) -> None:
        """焦点 pane 视图滚动（宿主代滚用）"""
        self.mux.scroll(delta)

    def scroll_pane(self, pane_id: int, delta: int) -> None:
        """指定 pane 视图滚动（滚轮命中该 pane 时宿主代滚）"""
        self.mux.pane_scroll(pane_id, delta)

    def scroll_to_bottom(self) -> None:
        """焦点 pane 回落到底部"""
        self.mux.scroll_to_bottom()

    # ---- 状态栏 ----
    def set_status(self, text: str) -> None:
        """设置状态栏文本（render 时变化才重画，参与增量）"""
        self.mux.set_status(text)

    # ---- 选区 ----
    def pane_selection_set(self, pane_id, anchor_x, anchor_y, end_x, end_y) -> None:
        """区域选择（整屏坐标）：anchor → end"""
        self.mux.pane_selection_set(pane_id, anchor_x, anchor_y, end_x, end_y)

    def pane_selection_select_word(self, pane_id, x, y) -> None:
        """双击选词（整屏坐标）"""
        self.mux.pane_selection_select_word(pane_id, x, y)

    def pane_selection_select_line(self, pane_id, x, y) -> None:
        """三击选行（整屏坐标）"""
        self.mux.pane_selection_select_line(pane_id, x, y)

    def pane_selection_text(self, pane_id) -> str:
        return self.mux.pane_selection_text(pane_id)

    def pane_selection_active(self, pane_id) -> bool:
        return bool(self.mux.pane_selection_active(pane_id))

    def pane_selection_clear(self, pane_id) -> None:
        self.mux.pane_selection_clear(pane_id)

    def set_focus_selection_callback(self, callback) -> None:
        """应用 OSC 52 写剪贴板回调（焦点 pane）"""
        self.mux.set_focus_selection_callback(callback)

    def send_paste(self, text: str) -> None:
        """模式感知粘贴下发到焦点 pane（bracketed paste 自动包裹）"""
        self.mux.send_paste(text)

    # ---- 渲染 / 查询 ----
    def render(self):
        """增量渲染：合成所有 pane + 分隔线 + 状态栏 → (bytes, cursor_row, cursor_col, visible)"""
        return self.mux.render()

    def cursor_seq(self, row: int, col: int, visible: bool) -> str:
        """光标定位序列（0-based → 1-based CUP + show/hide），pywezterm 生成"""
        return pywezterm.cursor_seq(row, col, visible)

    def pane_text(self, pane_id: int) -> str:
        return self.mux.pane_text(pane_id)

    def close(self) -> None:
        """关闭所有 pane（终止子进程 + 释放）"""
        self.mux.close()


class Pane:
    """单 pane Mux 薄封装（独立场景 / test_e2e 复用）。

    内部是一个只含一个 pane 的 Mux，方法直接委托到该 pane。
    """

    def __init__(self, cols: int, rows: int, argv, cwd=None, env=None, scrollback: int = 10000):
        self.name = os.path.basename(argv[0])
        self.mux = pywezterm.Mux(cols, rows)
        self.mux.add_pane(argv, cwd, env)
        self._id = 0
        self._size = (cols, rows)

    @property
    def eof(self) -> bool:
        return self.mux.pane_try_wait(self._id) is not None

    def write(self, data: bytes) -> None:
        self.mux.pane_write(self._id, data)

    def resize(self, cols: int, rows: int) -> None:
        self.mux.pane_resize(self._id, cols, rows)
        self._size = (cols, rows)

    def key_down(self, key: str, mods: int) -> bytes:
        return bytes(self.mux.pane_key_down(self._id, key, mods))

    def key_up(self, key: str, mods: int) -> bytes:
        return bytes(self.mux.pane_key_up(self._id, key, mods))

    def mouse(self, x: int, y: int, kind: str, button: str, mods: int) -> bytes:
        return bytes(self.mux.pane_mouse(self._id, x, y, kind, button, mods))

    def scroll(self, delta: int) -> None:
        self.mux.pane_scroll(self._id, delta)

    def scroll_to_bottom(self) -> None:
        self.mux.pane_scroll_to_bottom(self._id)

    def is_mouse_grabbed(self) -> bool:
        return bool(self.mux.pane_is_mouse_grabbed(self._id))

    def text(self) -> str:
        return self.mux.pane_text(self._id)

    def cursor(self):
        return self.mux.pane_cursor(self._id)

    def get_size(self):
        """pty 尺寸 (cols, rows)（测试用）"""
        return self._size

    def close(self) -> None:
        self.mux.close()