"""输入路由用例：按键/鼠标/尺寸事件的领域逻辑（含分割线拖拽与文本选区状态机）。

路由统一交给 Mux 门面：键盘走 mux.key_down/key_up（焦点 pane），鼠标走
mux.mouse（整屏坐标命中路由 + 坐标换算），滚动走 mux.scroll_pane。
用例层只负责事件归一化分发 + 状态机：
- 分割线拖拽（press 命中分隔线 → move 预览 → release 一次 resize）
- 文本选区（press 起点 → move 更新终点 → release 取文写剪贴板；
  双击选词 / 三击选行）
- 粘贴（Ctrl+V → 读系统剪贴板 → 模式感知下发）
"""

import time
from typing import Optional, Tuple

from leaf.domain.events import KeyEvent, MOD_CTRL, MouseEvent, ResizeEvent
from leaf.usecases.ports import ClipboardPort, ConsolePort, MuxPanelPort

MOVE_INTERVAL = 0.016  # 鼠标 move 转发节流：≤60Hz，防高频事件刷爆渲染
KEY_F9 = "F9"
KEY_F10 = "F10"
WHEEL_LINES = 3  # 宿主代滚步长
MIN_PANE = 8     # 分割列最小边距

# 拖拽状态：None 或 (起始鼠标 x, 起始 split_col)
DragState = Optional[Tuple[int, int]]
# 选区拖拽状态：None 或 (pane_id, 锚点 x, 锚点 y)
SelDragState = Optional[Tuple[int, int, int]]


def handle_key(mux: MuxPanelPort, focus: int, ev: KeyEvent,
               clipboard: Optional[ClipboardPort] = None) -> Tuple[int, bool]:
    """路由一次按键：F9/F10 面板快捷键、Ctrl+V 宿主粘贴，其余转发焦点 pane。

    返回 (focus, exit)。
    """
    if not ev.down:
        return focus, False
    if ev.key == KEY_F9 and ev.mods == 0:
        focus = 1 - focus
        mux.set_focus(focus)
        return focus, False
    if ev.key == KEY_F10 and ev.mods == 0:
        return focus, True
    if ev.key == "v" and ev.mods == MOD_CTRL and clipboard is not None:
        # 宿主粘贴：读系统剪贴板 → 模式感知下发（bracketed paste 自动包裹）
        text = clipboard.read()
        if text:
            mux.scroll_to_bottom()
            mux.send_paste(text)
        return focus, False
    mux.scroll_to_bottom()  # 键盘输入即恢复贴底（退出滚动查看历史状态）
    mux.key_down(ev.key, ev.mods)
    mux.key_up(ev.key, ev.mods)
    return focus, False


def handle_mouse(mux: MuxPanelPort, focus: int, cols: int, rows: int, ev: MouseEvent) -> int:
    """路由一次鼠标事件：Mux 按整屏坐标命中 pane 并换算；按击（press 非滚轮）切焦点。

    滚轮/移动只转发命中的窗格，不动焦点；程序未启用鼠标模式（编码为空）时的
    滚轮由宿主滚动该窗格 scrollback；状态栏行（最底行）不响应鼠标。
    """
    if ev.y >= rows - 1:
        return focus
    hit = mux.pane_at(ev.x, ev.y)
    if hit is None:
        return focus  # 分隔线/未命中：不转发
    if ev.button.startswith("wheel"):
        # 应用接管鼠标（DECSET 鼠标追踪）则转发；否则宿主代滚该窗格 scrollback
        if mux.pane_is_mouse_grabbed(hit):
            mux.mouse(ev.x, ev.y, ev.kind, ev.button, ev.mods)
        else:
            mux.scroll_pane(hit, WHEEL_LINES if ev.button == "wheel_up" else -WHEEL_LINES)
        return focus
    mux.mouse(ev.x, ev.y, ev.kind, ev.button, ev.mods)
    if ev.kind == "press":
        mux.set_focus(hit)
        # 按击只切焦点，不回落底部（滚动查看历史时点击不丢失位置）
        return hit
    return focus


def handle_events(mux: MuxPanelPort, console: ConsolePort, focus: int, cols: int, rows: int,
                  split_col: int, drag: DragState, last_move: float,
                  sel_drag: SelDragState = None,
                  clipboard: Optional[ClipboardPort] = None):
    """处理全部待处理控制台事件；含分割线拖拽、文本选区、双击/三击、悬停转发。

    drag: None 或 (起始鼠标 x, 起始 split_col)，按下分割线时进入拖拽；
    last_move: 上次转发 move 的时间戳（move 事件高频，16ms 节流）；
    sel_drag: None 或 (pane_id, 锚点 x, 锚点 y)，左键按下 pane 时进入选区；
    clipboard: 可选，Ctrl+V 粘贴与选区复制用。
    返回 (focus, exit, cols, rows, dirty, split_col, drag, last_move, force_full,
          sel_drag)。
    """
    dirty = False
    force_full = False
    for ev in console.read_inputs():
        if isinstance(ev, KeyEvent):
            focus, exit_ = handle_key(mux, focus, ev, clipboard)
            if exit_:
                return (focus, True, cols, rows, True, split_col, drag, last_move,
                        force_full, sel_drag)
        elif isinstance(ev, MouseEvent):
            if ev.kind == "move":
                if drag is not None:
                    # 分割线拖拽：只更新分割位置（Mux 预览重算矩形），实时不 resize
                    # 子 ConPTY（窄化会把超宽历史行 wrap 裂开）；松手时一次落位。
                    new_split = max(MIN_PANE, min(cols - 1 - MIN_PANE, drag[1] + (ev.x - drag[0])))
                    if new_split != split_col:
                        split_col = new_split
                        mux.set_split_col(split_col)
                    dirty = True
                elif sel_drag is not None:
                    # 选区拖拽：更新终点（16ms 节流防刷爆）
                    if time.monotonic() - last_move >= MOVE_INTERVAL:
                        last_move = time.monotonic()
                        mux.pane_selection_set(sel_drag[0], sel_drag[1], sel_drag[2],
                                               ev.x, ev.y)
                        dirty = True
                else:
                    # 悬停：转发给命中的窗格（16ms 节流防刷爆），不切焦点
                    if time.monotonic() - last_move >= MOVE_INTERVAL:
                        last_move = time.monotonic()
                        focus = handle_mouse(mux, focus, cols, rows, ev)
                        dirty = True
                continue
            if ev.kind == "press" and ev.button == "left" and ev.x == split_col:
                drag = (ev.x, split_col)  # 命中分割线：进入拖拽不转发应用
                dirty = True
                continue
            if ev.kind == "press" and ev.button == "left":
                hit = mux.pane_at(ev.x, ev.y)
                if hit is None:
                    dirty = True  # 状态栏/未命中：不选区不转发
                    continue
                if mux.pane_is_mouse_grabbed(hit):
                    # 应用接管鼠标（如 vim）：转发应用 + 切焦点，不选区
                    focus = handle_mouse(mux, focus, cols, rows, ev)
                    dirty = True
                    continue
                # 宿主选区：按点击次数分发（双击选词 / 三击选行 / 单击起点）
                if ev.count == 2:
                    mux.pane_selection_select_word(hit, ev.x, ev.y)
                    sel_drag = None
                elif ev.count == 3:
                    mux.pane_selection_select_line(hit, ev.x, ev.y)
                    sel_drag = None
                else:
                    mux.pane_selection_set(hit, ev.x, ev.y, ev.x, ev.y)
                    sel_drag = (hit, ev.x, ev.y)
                mux.set_focus(hit)
                # 单击只切换焦点/起选区，不回落底部：滚动查看历史时点击
                # 终端不应丢失滚动位置（与官方终端一致）
                focus = hit
                dirty = True
                continue
            if ev.kind == "release":
                if drag is not None:
                    # 分割线拖拽结束：一次 resize 到位 + 收敛帧
                    mux.resize(cols, rows)
                    force_full = True
                    drag = None
                    dirty = True
                    continue
                if sel_drag is not None:
                    # 选区拖拽结束：取文 → 写系统剪贴板
                    pane_id, _, _ = sel_drag
                    text = mux.pane_selection_text(pane_id)
                    if clipboard is not None and text:
                        clipboard.write(text)
                    sel_drag = None
                    dirty = True
                    continue
                focus = handle_mouse(mux, focus, cols, rows, ev)
                dirty = True
            else:
                focus = handle_mouse(mux, focus, cols, rows, ev)
                dirty = True
        elif isinstance(ev, ResizeEvent):
            cols, rows = console.size()
            mux.resize(cols, rows)
            dirty = True
            force_full = True  # 行数变化：旧帧残行须清屏全量重建
    return focus, False, cols, rows, dirty, split_col, drag, last_move, force_full, sel_drag
