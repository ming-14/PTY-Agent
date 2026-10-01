"""GuiWindowMonitor — GUI 窗口检测器（事件驱动 + 低频兜底扫描）

两条互补路径：

1. **事件驱动（主路径，毫秒级）**：对 Job 进程树中的每个 PID 装载
   `SetWinEventHook(EVENT_MIN..EVENT_MAX, WINEVENT_OUTOFCONTEXT)`。窗口有
   可访问性变化（创建/显示等）时系统立即回调本模块，无需扫描。
   回调内不判读 event 号，改用「可见 + 非 WS_CHILD」校验决定是否算作新的
   GUI 窗口 —— 因此 CREATE 事件（窗口尚不可见）自然被拒，随后的 SHOW 通过。
   按 PID 装载（而非全系统）避免收到 Explorer / 浏览器等无关进程的洪水。
   - `WINEVENT_OUTOFCONTEXT` 的回调**只投递给装载该 hook 的线程**，且在该
     线程抽取消息队列时才被调用 → 需要一条专用消息泵线程，hook 的装载与
     卸载都在该线程完成；新 PID 通过 `PostThreadMessageW` 转交给它。
2. **兜底扫描（辅路径，默认 5s）**：`EnumWindows` 遍历 + Job PID 比对。
   子进程完全可能在 Job 的 IOCP 新进程通知送达、hook 装载完成之前就把窗口
   显示出来（"启动即弹窗"很常见），那一刻事件已错过，只能靠扫描补齐。
   兜底扫描同时充当事件路径丢事件时的自愈机制。

新 PID 来源：`ProcessJob` 的 IOCP `NEW_PROCESS` 通知
（`job.set_pid_listener(self.attach_pid)`）。

其它特性：
- 基于 hwnd 去重，同一窗口只上报一次（事件与扫描两条路径共享去重集合）
- 标题用 `SendMessageTimeoutW(WM_GETTEXT, SMTO_ABORTIFHUNG)` 读取：
  `GetWindowTextW` 对其它进程的窗口会无条件等待，目标 UI 线程假死时会
  把本线程一起挂住；带超时后最坏只损失标题字段，不再拖死检测
- 通过 `SendMessage(WM_CLOSE)` 关闭指定窗口
- 线程安全（内部锁保护状态）；可随时 `clear()` 重置去重状态强制全量重扫
"""

import ctypes
import logging
import threading
import time
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Dict, List, Optional, Set
from ctypes import wintypes as W

from .convars import (
    _EnumWindows,
    _GetWindowThreadProcessId,
    _GetClassNameW,
    _IsWindowVisible,
    _SendMessageW,
    _SendMessageTimeoutW,
    _SetWinEventHook,
    _UnhookWinEvent,
    _GetWindowStyle,
    _GetCurrentThreadId,
    _PostThreadMessageW,
    _GetMessageW,
    _PeekMessageW,
    PM_NOREMOVE,
    _TranslateMessage,
    _DispatchMessageW,
    MSG,
    WNDENUMPROC,
    WINEVENTPROC,
    WM_CLOSE,
    WM_GETTEXT,
    WM_QUIT,
    WM_APP_INSTALL_HOOK,
    WM_APP_REMOVE_HOOK,
    EVENT_MIN,
    EVENT_MAX,
    WINEVENT_OUTOFCONTEXT,
    WINEVENT_SKIPOWNPROCESS,
    OBJID_WINDOW,
    CHILDID_SELF,
    GWL_STYLE,
    WS_CHILD,
    SMTO_ABORTIFHUNG,
)
from .job import ProcessJob

_logger = logging.getLogger("backend-gui-monitor")

_WINDOW_TITLE_MAX = 256
_WINDOW_CLASS_MAX = 256
_GETTEXT_TIMEOUT_MS = 200       # 目标窗口假死时最多损失这么久，不阻塞检测线程
_SWEEP_INTERVAL = 5.0           # 兜底扫描周期（秒）—— 事件路径已覆盖常态


@dataclass
class GuiWindowInfo:
    """GUI 窗口信息

    Attributes:
        hwnd:       窗口句柄（整数值）。
        pid:        拥有该窗口的进程 PID。
        title:      窗口标题。
        class_name: 窗口类名。
    """
    hwnd: int
    pid: int
    title: str
    class_name: str

    def to_dict(self) -> Dict:
        """转换为字典（用于 JSON 序列化）"""
        return {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "title": self.title,
            "class_name": self.class_name,
        }


class GuiWindowMonitor:
    """GUI 窗口检测器（事件驱动 + 兜底扫描）

    Attributes:
        windows: 已检测到的所有窗口列表。
    """

    def __init__(self, job: Optional[ProcessJob] = None,
                 sweep_interval: float = _SWEEP_INTERVAL):
        """初始化 GUI 窗口检测器

        Args:
            job: 关联的 ProcessJob 实例。为 None 时检测无操作。
            sweep_interval: 兜底扫描周期（秒）。
        """
        self._job = job
        # 必须可重入：_sweep 持锁期间调用 EnumWindows，回调 _enum_proc 在
        # **同一线程**再次取锁 —— 用普通 Lock 会自死锁（泵线程取锁不受影响）。
        self._lock = RLock()
        self._known_hwnds: Set[int] = set()      # 去重（事件与扫描共用）
        self._windows: List[GuiWindowInfo] = []  # 全量已检出
        self._pending: List[GuiWindowInfo] = []  # 待上层取走（不重复上报）

        # EnumWindows / WinEvent 回调 —— 必须保持引用防止 GC
        self._enum_cb: WNDENUMPROC = WNDENUMPROC(self._enum_proc)
        self._win_cb: WINEVENTPROC = WINEVENTPROC(self._event_proc)

        # 临时缓冲区（EnumWindows 回调中使用）
        self._temp_target_pids: Set[int] = set()
        self._temp_new_windows: List[GuiWindowInfo] = []

        # 消息泵线程与 hook 表
        self._pump: Optional[threading.Thread] = None
        self._pump_tid: int = 0
        self._pump_ready = threading.Event()
        self._stop = threading.Event()
        self._closed = False
        self._hooks: Dict[int, int] = {}         # pid -> HWINEVENTHOOK
        self._want_pids: Set[int] = set()        # 待泵线程装载的 PID
        self._drop_pids: Set[int] = set()        # 待泵线程卸载的 PID
        self._cb_seen = 0                        # 收到的 WinEvent 计数（诊断）

        # 扫描节流
        self._sweep_interval = sweep_interval
        self._last_sweep = 0.0

        # 上层唤醒回调（GuiDetector 注册，用于立即唤醒等待循环）
        self._listener: Optional[Callable[[], None]] = None

        if job is not None:
            # Job 的 IOCP 新进程通知 → 逐 PID 装载 hook；进程退出 → 卸载
            try:
                job.set_pid_listener(self.attach_pid)
                job.set_exit_listener(self.detach_pid)
            except AttributeError:
                _logger.debug("Job 不支持 pid listener，仅依赖兜底扫描")
            # 已存在的进程先挂上（assign 可能早于本对象构造）
            try:
                for pid in job.query_process_list():
                    self.attach_pid(pid)
            except Exception as e:
                _logger.debug("初始 PID 装载失败: %s", e)

    # ════════════════════════════════════════════════════════════
    # 事件驱动路径
    # ════════════════════════════════════════════════════════════

    def _post_to_pump(self, msg_id: int) -> bool:
        """向泵线程投递一条控制消息（队列未就绪时短暂重试）

        PostThreadMessage 只有在目标线程已创建消息队列后才可靠，故对失败
        做有界重试；仍失败则记 WARNING（此时兜底扫描仍可覆盖检测）。
        """
        for _ in range(20):                 # 最多 ~100ms
            tid = self._pump_tid
            if tid and _PostThreadMessageW(tid, msg_id, 0, 0):
                return True
            self._stop.wait(0.005)
        _logger.warning(
            "GUI 消息泵投递失败 (msg=0x%X)，新窗口将依赖兜底扫描发现",
            msg_id)
        return False

    def attach_pid(self, pid: int) -> None:
        """为进程树新增 PID 装载窗口事件 hook（线程安全，可在任意线程调用）

        hook 必须在泵线程上装载，故这里只登记 PID 并转交泵线程执行。
        """
        if not pid or self._closed:
            return
        with self._lock:
            if pid in self._hooks or pid in self._want_pids:
                return
            self._want_pids.add(int(pid))
        self._ensure_pump()
        self._post_to_pump(WM_APP_INSTALL_HOOK)

    def detach_pid(self, pid: int) -> None:
        """进程退出后卸载其 hook（由 Job 退出通知驱动，避免长时间会话累积泄漏）"""
        if not pid:
            return
        with self._lock:
            if pid not in self._hooks:
                self._want_pids.discard(int(pid))
                return
            self._drop_pids.add(int(pid))
        self._post_to_pump(WM_APP_REMOVE_HOOK)

    def _ensure_pump(self) -> None:
        """惰性启动消息泵线程（首次需要 hook 时才建线程）"""
        if self._pump is not None or self._closed:
            return
        self._pump_ready.clear()
        t = threading.Thread(
            target=self._pump_loop, daemon=True,
            name=f"gui-winevent-{id(self)}",
        )
        self._pump = t
        t.start()
        # 等泵线程就绪并拿到 TID，否则 PostThreadMessage 无处可投
        if not self._pump_ready.wait(timeout=2.0):
            _logger.warning("GUI 消息泵启动超时，仅依赖兜底扫描")

    def _pump_loop(self) -> None:
        """消息泵线程：装载/卸载 hook 并接收 WinEvent 回调

        OUTOFCONTEXT 回调在 `GetMessageW` 内部被调用，因此本线程必须持续
        阻塞在消息获取上；WM_QUIT 结束循环并卸载全部 hook。
        """
        self._pump_tid = _GetCurrentThreadId()
        msg = MSG()
        # 先创建本线程消息队列：否则 PostThreadMessage 会在队列存在前投递失败
        _PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        # 排队中的 PID 立即装载（不依赖消息投递，覆盖构造期注册的常见情形）
        self._install_pending_hooks()
        self._pump_ready.set()
        while not self._stop.is_set():
            try:
                ret = _GetMessageW(ctypes.byref(msg), None, 0, 0)
            except Exception as e:
                _logger.warning("GUI 消息泵异常: %s", e)
                break
            if ret == 0 or ret == -1:       # WM_QUIT 或错误
                break
            if msg.message == WM_APP_INSTALL_HOOK:
                self._install_pending_hooks()
                continue
            if msg.message == WM_APP_REMOVE_HOOK:
                self._drop_pending_hooks()
                continue
            try:
                _TranslateMessage(ctypes.byref(msg))
                _DispatchMessageW(ctypes.byref(msg))
            except Exception:
                pass
        self._unhook_all()

    def _install_pending_hooks(self) -> None:
        """（泵线程）为所有待注册 PID 装载全范围 WinEvent hook

        范围必须是 EVENT_MIN..EVENT_MAX：实测把 eventMin/Max 限定在
        0x80000000+ 的 object 事件区间一条都收不到。
        """
        with self._lock:
            pids = list(self._want_pids)
            self._want_pids.clear()
        for pid in pids:
            if self._closed or self._stop.is_set():
                return
            try:
                hook = _SetWinEventHook(
                    EVENT_MIN, EVENT_MAX, None, self._win_cb,
                    pid, 0,
                    WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
                )
            except Exception as e:
                _logger.debug("SetWinEventHook(pid=%d) 异常: %s", pid, e)
                continue
            if hook:
                with self._lock:
                    self._hooks[pid] = int(hook)
                _logger.debug("已装载 GUI 事件 hook: pid=%d", pid)
            else:
                # 进程可能已退出：属正常竞态，兜底扫描仍会覆盖
                _logger.debug("SetWinEventHook(pid=%d) 失败 err=%d",
                              pid, ctypes.get_last_error())

    def _drop_pending_hooks(self) -> None:
        """（泵线程）卸载已退出进程的 hook —— 卸载必须在装载它的线程执行"""
        with self._lock:
            pids = list(self._drop_pids)
            self._drop_pids.clear()
            hooks = [(p, self._hooks.pop(p, None)) for p in pids]
        for pid, hook in hooks:
            if hook:
                try:
                    _UnhookWinEvent(hook)
                    _logger.debug("已卸载 GUI 事件 hook: pid=%d", pid)
                except Exception as e:
                    _logger.debug("UnhookWinEvent(pid=%d) 异常: %s", pid, e)

    def _unhook_all(self) -> None:
        """（泵线程）卸载全部 hook"""
        with self._lock:
            hooks = list(self._hooks.values())
            self._hooks.clear()
        for h in hooks:
            try:
                _UnhookWinEvent(h)
            except Exception:
                pass

    def _event_proc(self, hhook, event, hwnd, id_object, id_child,
                    thread_id, time_ms) -> None:
        """WinEvent 回调（泵线程）：hwnd 校验 → 入队 → 唤醒上层

        不判读 `event`：实测该值以 `EVENT_OBJECT_xxx >> 16` 的形式送达
        （SHOW→0x8002、CREATE→0x8000 …），且窄范围挂钩收不到任何事件，
        故统一挂全范围、把回调仅当作"该 hwnd 发生了可访问性变化"的信号，
        由下面的可见性 + 样式校验决定是不是一个新出现的可见顶层窗口。
        这反而更稳：CREATE 时窗口尚不可见被拒，随后 SHOW 时通过。
        """
        self._cb_seen += 1
        if self._closed or not hwnd:
            return
        # 只关心窗口对象本身（非菜单/滚动条/列表项等子元素）
        if id_object != OBJID_WINDOW or id_child != CHILDID_SELF:
            return
        try:
            if not _IsWindowVisible(hwnd):
                return
            if _GetWindowStyle(hwnd, GWL_STYLE) & WS_CHILD:
                return          # 子窗口不算独立 GUI 窗口（与 EnumWindows 对齐）
        except Exception as e:
            _logger.debug("WinEvent 回调校验异常: %s", e)
            return
        info = self._build_info(hwnd)
        if info is None:
            return
        _logger.debug("WinEvent 回调命中: hwnd=0x%X event=%#x",
                      hwnd, event)
        self._publish(info, source="event")

    # ════════════════════════════════════════════════════════════
    # 兜底扫描路径
    # ════════════════════════════════════════════════════════════

    def poll(self) -> List[GuiWindowInfo]:
        """取走事件路径结果 + 按节流执行一次 EnumWindows 兜底扫描

        Returns:
            本轮新增的窗口列表（事件与扫描合并，已去重）。
        """
        out = self.take_pending()
        out.extend(self._sweep(force=False))
        return out

    def sweep_now(self) -> List[GuiWindowInfo]:
        """立即全量扫描（忽略节流），用于首轮基线建立。"""
        return self._sweep(force=True)

    def _sweep(self, force: bool) -> List[GuiWindowInfo]:
        """EnumWindows 扫描 Job 进程树的可见顶层窗口，返回本轮新增。"""
        if not self._job or self._closed:
            return []
        now = time.monotonic()
        if not force and (now - self._last_sweep) < self._sweep_interval:
            return []
        self._last_sweep = now

        target_pids = set(self._job.query_process_list())
        if not target_pids:
            return []

        with self._lock:
            self._temp_target_pids = target_pids
            self._temp_new_windows = []

            ok = _EnumWindows(self._enum_cb, 0)
            if not ok:
                err = ctypes.get_last_error()
                if err != 0:
                    _logger.debug("EnumWindows 失败: err=%d", err)

            new_windows = list(self._temp_new_windows)
            self._windows.extend(new_windows)
            return new_windows

    def _enum_proc(self, hwnd: int, lparam: int) -> bool:
        """EnumWindows 回调 — 检查窗口是否属于 Job 内进程"""
        if not _IsWindowVisible(hwnd):
            return True

        with self._lock:
            if hwnd in self._known_hwnds:
                return True
            self._known_hwnds.add(hwnd)

        pid = W.DWORD(0)
        _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in self._temp_target_pids:
            with self._lock:
                self._known_hwnds.discard(hwnd)   # 非本 Job 进程，不占用去重名额
            return True

        info = GuiWindowInfo(
            hwnd=hwnd,
            pid=pid.value,
            title=_window_title(hwnd),
            class_name=_window_class(hwnd),
        )
        self._temp_new_windows.append(info)
        _logger.info("检测到 GUI 窗口(扫描): hwnd=0x%X pid=%d title=%r class=%s",
                     hwnd, pid.value, info.title, info.class_name)
        return True

    # ════════════════════════════════════════════════════════════
    # 共享：判定 / 入队 / 取走
    # ════════════════════════════════════════════════════════════

    def _build_info(self, hwnd) -> Optional[GuiWindowInfo]:
        """事件路径的窗口信息构造（hwnd 已判定为可见顶层）"""
        pid = W.DWORD(0)
        try:
            _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        except Exception:
            return None
        hwnd_int = int(hwnd)
        with self._lock:
            if hwnd_int in self._known_hwnds:
                return None
            self._known_hwnds.add(hwnd_int)
        info = GuiWindowInfo(
            hwnd=hwnd_int,
            pid=pid.value,
            title=_window_title(hwnd),
            class_name=_window_class(hwnd),
        )
        _logger.info("检测到 GUI 窗口(事件): hwnd=0x%X pid=%d title=%r class=%s",
                     info.hwnd, info.pid, info.title, info.class_name)
        return info

    def _publish(self, info: GuiWindowInfo, source: str) -> None:
        """把窗口放入待取队列并唤醒上层监听者"""
        with self._lock:
            self._windows.append(info)
            self._pending.append(info)
            listener = self._listener
        if listener is not None:
            try:
                listener()
            except Exception as e:
                _logger.debug("GUI 监听回调异常(%s): %s", source, e)

    def take_pending(self) -> List[GuiWindowInfo]:
        """取走事件路径已检出但尚未上报的窗口（一次性，取后即空）"""
        with self._lock:
            out, self._pending = self._pending, []
        return out

    def set_listener(self, cb: Optional[Callable[[], None]]) -> None:
        """注册"有新窗口待取"回调（GuiDetector 用它即时唤醒等待循环）

        注册时若已有未取走的窗口，立即回调一次，避免订阅前产生的事件丢失。
        """
        with self._lock:
            self._listener = cb
            has_pending = bool(self._pending)
        if cb is not None and has_pending:
            try:
                cb()
            except Exception as e:
                _logger.debug("GUI 监听初次回调异常: %s", e)

    # ════════════════════════════════════════════════════════════
    # 查询与控制
    # ════════════════════════════════════════════════════════════

    @property
    def windows(self) -> List[GuiWindowInfo]:
        """获取所有已检测到的窗口"""
        with self._lock:
            return list(self._windows)

    def close_window(self, hwnd: int) -> bool:
        """通过 SendMessage(WM_CLOSE) 关闭指定窗口"""
        try:
            _SendMessageW(hwnd, WM_CLOSE, 0, 0)
            _logger.info("已发送 WM_CLOSE 到窗口 hwnd=0x%X", hwnd)
            return True
        except Exception as e:
            _logger.warning("关闭窗口 hwnd=0x%X 失败: %s", hwnd, e)
            return False

    def clear(self):
        """清空去重状态和窗口记录

        调用后，下一轮扫描() 将重新上报所有现有窗口。
        """
        with self._lock:
            self._known_hwnds.clear()
            self._windows.clear()
            self._pending.clear()
        self._last_sweep = 0.0

    def close(self):
        """停止消息泵并卸载全部 hook（幂等）"""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        tid = self._pump_tid
        pump = self._pump
        if pump is not None:
            if tid:
                try:
                    _PostThreadMessageW(tid, WM_QUIT, 0, 0)
                except Exception:
                    pass
            if pump is not threading.current_thread():
                pump.join(timeout=2.0)
                if pump.is_alive():
                    _logger.warning("GUI 消息泵未在 2s 内退出")
            self._pump = None
        self._unhook_all()
        with self._lock:
            self._known_hwnds.clear()
            self._windows.clear()
            self._pending.clear()
            self._listener = None
        self._job = None


# ════════════════════════════════════════════════════════════════
# 模块级工具
# ════════════════════════════════════════════════════════════════

def _window_title(hwnd) -> str:
    """读取窗口标题 —— 必须带超时

    `GetWindowTextW` 对其它进程的窗口会 SendMessage(WM_GETTEXT) 并**无限**
    等待回复：目标 UI 线程一旦假死就会把调用线程一起挂住，导致 GUI 检测
    （及其唤醒）永久静默。这里改用 SendMessageTimeoutW + SMTO_ABORTIFHUNG，
    最坏只损失标题字段。
    """
    buf = ctypes.create_unicode_buffer(_WINDOW_TITLE_MAX)
    try:
        ok = _SendMessageTimeoutW(
            hwnd, WM_GETTEXT, _WINDOW_TITLE_MAX - 1,
            ctypes.addressof(buf),      # 必须用地址：byref 结果无法可靠 cast
            SMTO_ABORTIFHUNG, _GETTEXT_TIMEOUT_MS, None,
        )
    except Exception as e:
        # 不静默吞掉：漏导入/参数错会让所有窗口标题恒为空
        _logger.debug("SendMessageTimeoutW(WM_GETTEXT) 失败: %s", e)
        return ""
    if not ok:
        return ""
    return buf.value or ""


def _window_class(hwnd) -> str:
    """读取窗口类名（直接查询窗口对象，不跨线程发消息，无挂死风险）"""
    buf = ctypes.create_unicode_buffer(_WINDOW_CLASS_MAX)
    try:
        if not _GetClassNameW(hwnd, buf, _WINDOW_CLASS_MAX):
            return ""
    except Exception:
        return ""
    return buf.value or ""
