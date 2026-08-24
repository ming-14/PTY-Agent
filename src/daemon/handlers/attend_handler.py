"""attend 命令处理器 — CLI 接管会话为完整实时终端

会话输出以原始字节流透传客户端，由用户终端原生渲染（与直接运行一致）；
输入/按键/鼠标/resize 帧经本 handler 路由到会话。

设计要点：
- publisher 输出回调只入有界队列（非阻塞）：回调运行在会话读者线程上，
  阻塞会拖慢该会话的输出管线与触发检测，故绝不在此路径做网络 IO。
- 独立发送线程 drain 队列 + 轮询鼠标追踪状态（mode 驱动 quick-edit）。
- 不消费共享输出游标、不触碰会话生命周期 → 不影响 web 端与其他 CLI 读。
- 帧走既有信封 + 签名（发送线程需重设线程局部签名器与响应包装器）。
"""

import collections
import threading

from ...logging import get_logger
from ...protocol.envelope import (
    unwrap as _env_unwrap,
    wrap_response as _env_wrap_response,
)
from ...protocol.message import Message
from .base import DaemonHandler
from ...execution.context import HandlerContext

_logger = get_logger("pty-daemon")

# 原始输出转发队列上限（字节）。溢出丢最旧块并触发一次全屏重同步，
# 仅在刷屏类洪峰（如 yes）出现；正常交互零影响。
_ATTEND_MAX_QUEUE = 4 * 1024 * 1024
# 发送线程空闲轮询间隔（秒）
_IDLE_POLL = 0.2
# 单帧原始输出上限（字节）：PTY_READ_SIZE 即读块上限，此处兜底避免超
# MAX_MESSAGE_LENGTH（1MB）导致客户端 recv 失败
_MAX_FRAME = 65536


def _bytes_to_text(data: bytes) -> str:
    """原始字节 → latin-1 字符串（1:1 无损映射，JSON 可安全传输，逆映射见客户端）"""
    return data.decode("latin-1")


class AttendHandler(DaemonHandler):
    """attend 命令处理器：校验 + 委托 _AttendSession 管理长连接"""

    def handle(self, ctx: HandlerContext, conn, msg: dict):
        session_id = msg.get("id", "")
        if not session_id:
            Message.send(conn, {"type": "error", "message": "Missing session id"})
            return
        session = ctx.manager.get_session(session_id)
        if not session:
            Message.send(
                conn, {"type": "error", "message": f"Session '{session_id}' not found"}
            )
            return
        if not session.running:
            Message.send(
                conn,
                {"type": "error", "message": f"Session '{session_id}' is not running"},
            )
            return
        _AttendSession(ctx, conn, session, msg).run()


class _AttendSession:
    """单个 attend 连接的会话：订阅 + 发送线程 + 输入帧路由"""

    def __init__(self, ctx: HandlerContext, conn, session, msg: dict):
        self._ctx = ctx
        self._conn = conn
        self._session = session
        self._sid = session.id
        self._is_sub = getattr(session, "mode", "pty") == "subprocess"
        self._msg = msg

        # 输出转发队列（读者线程写入 / 发送线程 drain）
        self._out_queue = collections.deque()
        self._out_len = 0
        self._out_lock = threading.Lock()
        # OSC 52 剪贴板写队列（reader 线程回调入队 / 发送线程 drain）
        self._clipboard_queue = collections.deque()
        self._screen_event = threading.Event()
        self._lost = False

        self._stop = threading.Event()
        self._ended = threading.Event()
        self._ended_info = {}

        # 鼠标追踪状态（变化时推送 attend_mouse_mode）
        self._last_mouse_tracking = None

        self._held = False
        self._sender = None
        # 连接线程已设置线程局部签名器，捕获后在发送线程重设
        self._signer = Message.get_outbound_signer()

    # ── 生命周期 ────────────────────────────────────────────

    def run(self):
        session = self._session
        session.acquire_hold()
        self._held = True
        try:
            # 接管尺寸：客户端随 attend 请求上报自身窗口尺寸，一次到位
            if not self._is_sub:
                try:
                    cols = int(self._msg.get("cols") or 0)
                    rows = int(self._msg.get("rows") or 0)
                    if cols > 0 and rows > 0:
                        session.resize(cols, rows)
                except (TypeError, ValueError) as e:
                    _logger.warning("attend: 初始 resize 参数无效 sid=%s: %s", self._sid, e)
                except Exception as e:
                    _logger.warning("attend: 初始 resize 失败 sid=%s: %s", self._sid, e)

            # 订阅前生成 replay：快照必须反映订阅前的屏幕真相，
            # 否则订阅窗口内已入队的字节会被快照与原始流各应用一次（重复应用）
            self._replay_frame = self._build_replay()

            # 订阅输出（非阻塞回调）+ 会话结束回调
            session.publisher.subscribe(self._on_data)
            session.publisher.add_on_end_callback(self._on_end)

            # OSC 52 剪贴板写回调：应用发 OSC 52 → 入队推送给客户端
            # （回调运行在 reader 线程，仅入队不阻塞）
            if not self._is_sub:
                try:
                    session.set_clipboard_callback(self._on_clipboard)
                except Exception as e:
                    _logger.debug("attend: 设置剪贴板回调失败 sid=%s: %s", self._sid, e)

            # 发送线程：ready + replay + 持续 push 原始输出
            self._sender = threading.Thread(
                target=self._sender_loop,
                name=f"attend-send-{self._sid}",
                daemon=True,
            )
            self._sender.start()

            # 主循环：路由输入帧直到 detach / 断开 / 会话结束
            self._input_loop()
        finally:
            self._cleanup()

    def _cleanup(self):
        """退订、停发送线程、释放会话持有"""
        self._stop.set()
        self._screen_event.set()
        if self._sender is not None and self._sender is not threading.current_thread():
            self._sender.join(timeout=1.0)
        # 会话仍存在时退订（会话已结束则被移出活跃表，publisher 随释放置空）
        session = self._ctx.manager.get_session(self._sid)
        if session is not None:
            try:
                session.publisher.unsubscribe(self._on_data)
            except Exception:
                pass
            try:
                session.publisher.remove_on_end_callback(self._on_end)
            except Exception:
                pass
        if self._held:
            self._held = False
            try:
                self._session.release_hold()
            except Exception:
                pass

    # ── 输出订阅回调（读者线程，非阻塞）────────────────────

    def _on_data(self, data: bytes, stream: str):
        """原始输出入队（运行在会话读者线程，禁止阻塞）"""
        try:
            with self._out_lock:
                if self._out_len + len(data) > _ATTEND_MAX_QUEUE:
                    while (
                        self._out_queue
                        and self._out_len + len(data) > _ATTEND_MAX_QUEUE
                    ):
                        old = self._out_queue.popleft()
                        self._out_len -= len(old)
                    self._lost = True
                self._out_queue.append((data, stream))
                self._out_len += len(data)
        except Exception:
            return
        self._screen_event.set()

    def _on_end(self, ended_session):
        """会话结束回调：记录结束信息并唤醒发送线程"""
        self._ended_info = {
            "sessionId": self._sid,
            "exitCode": ended_session.exit_code,
            "errorMessage": ended_session.error_message,
        }
        self._ended.set()
        self._screen_event.set()

    def _on_clipboard(self, selection, data):
        """OSC 52 剪贴板写回调（reader 线程）：入队推送给客户端"""
        if selection == "clipboard" and data:
            self._clipboard_queue.append((selection, data))
            self._screen_event.set()

    # ── 发送线程 ────────────────────────────────────────────

    def _sender_loop(self):
        # 线程局部：与连接线程一致的签名器 + 出站响应包装
        Message.set_outbound_signer(self._signer)
        Message.set_outbound_response_wrapper(_env_wrap_response)
        try:
            self._send_ready()
            self._send(self._replay_frame)
            while not self._stop.is_set() and not self._ended.is_set():
                self._screen_event.wait(_IDLE_POLL)
                self._screen_event.clear()
                if self._ended.is_set():
                    break
                self._drain_output()
                self._drain_clipboard()
                self._maybe_push_mouse_mode()
            if self._ended.is_set():
                self._send({"type": "attend_ended", **self._ended_info})
                self._stop.set()
                try:
                    self._conn.close()
                except OSError:
                    pass
        except (ConnectionError, OSError):
            self._stop.set()

    def _send_ready(self):
        s = self._session
        tracking = False
        if not self._is_sub:
            try:
                tracking = bool(s.is_mouse_tracking())
            except Exception:
                tracking = False
        self._last_mouse_tracking = tracking
        try:
            pty_type = s.pty_type
        except Exception:
            pty_type = "none"
        self._send(
            {
                "type": "attend_ready",
                "sessionId": self._sid,
                "uid": s.uid,
                "cols": s.cols,
                "rows": s.rows,
                "mode": s.mode,
                "encoding": s.encoding or "utf-8",
                "running": s.running,
                "exitCode": s.exit_code,
                "ptyType": pty_type,
                "outputOffset": s.output_offset,
                "mouseTracking": tracking,
            }
        )

    def _build_replay(self) -> dict:
        """构建初始 replay 帧：pty=模式恢复+可见屏快照+光标；subprocess=stdout/stderr 尾部

        必须在订阅前调用：快照反映订阅前的屏幕真相，后续推送的原始字节是它的延续。
        """
        s = self._session
        if self._is_sub:
            enc = s.encoding or "utf-8"
            try:
                out_text = s.get_output(encoding=enc)[-_MAX_FRAME * 2 :]
            except Exception:
                out_text = ""
            try:
                err_text = s.get_err_output(encoding=enc)[-_MAX_FRAME * 2 :]
            except Exception:
                err_text = ""
            return {
                "type": "attend_replay",
                "sessionId": self._sid,
                "text": out_text,
                "stderr": err_text,
                "subprocess": True,
            }
        try:
            text = (
                (s.mode_restore_seq() or "")
                + (s.get_snapshot(keep_ansi=True) or "")
                + (s.get_cursor_seq() or "")
            )
        except Exception as e:
            _logger.warning("attend: replay 生成失败 sid=%s: %s", self._sid, e)
            text = ""
        return {
            "type": "attend_replay",
            "sessionId": self._sid,
            "text": text,
            "subprocess": False,
        }

    def _send_resync(self):
        """丢帧后的全屏重同步（仅 pty）：模式恢复 + 当前快照 + 光标"""
        if self._is_sub:
            return
        s = self._session
        try:
            text = (
                (s.mode_restore_seq() or "")
                + (s.get_snapshot(keep_ansi=True) or "")
                + (s.get_cursor_seq() or "")
            )
        except Exception:
            text = ""
        if text:
            self._send(
                {
                    "type": "attend_resync",
                    "sessionId": self._sid,
                    "text": text,
                }
            )

    def _drain_output(self):
        """排空输出队列并发送原始字节帧

        丢帧标记时：整队丢弃（其字节已由新的全屏快照覆盖）并发送重同步，
        避免已体现在快照中的字节被二次应用。
        """
        with self._out_lock:
            lost = self._lost
            self._lost = False
            chunks = list(self._out_queue)
            self._out_queue.clear()
            self._out_len = 0
        if lost:
            self._send_resync()
            return
        for data, stream in chunks:
            if not data:
                continue
            # 单帧兜底切块（读块 64KB 通常无需切）
            for i in range(0, len(data), _MAX_FRAME):
                self._send(
                    {
                        "type": "attend_output",
                        "sessionId": self._sid,
                        "text": _bytes_to_text(data[i : i + _MAX_FRAME]),
                        "stream": stream,
                    }
                )

    def _maybe_push_mouse_mode(self):
        """鼠标追踪状态变化 → attend_mouse_mode（客户端据此切换 quick-edit）"""
        if self._is_sub:
            return
        try:
            tracking = bool(self._session.is_mouse_tracking())
        except Exception:
            return
        if tracking != self._last_mouse_tracking:
            self._last_mouse_tracking = tracking
            self._send(
                {
                    "type": "attend_mouse_mode",
                    "sessionId": self._sid,
                    "tracking": tracking,
                }
            )

    def _drain_clipboard(self):
        """排空 OSC 52 剪贴板写队列 → attend_clipboard 帧"""
        if self._is_sub:
            return
        while True:
            try:
                selection, data = self._clipboard_queue.popleft()
            except IndexError:
                break
            self._send(
                {
                    "type": "attend_clipboard",
                    "sessionId": self._sid,
                    "selection": selection,
                    "data": data,
                }
            )

    def _send(self, frame: dict):
        Message.send(self._conn, frame)

    # ── 输入帧路由（连接线程）───────────────────────────────

    def _input_loop(self):
        conn = self._conn
        while not self._stop.is_set() and not self._ended.is_set():
            msg = Message.recv(conn)
            if msg is None:
                break
            try:
                _, body, _ = _env_unwrap(msg)
            except Exception:
                continue
            if body.get("type") == "attend_detach":
                break
            try:
                self._route(body)
            except Exception as e:
                _logger.warning(
                    "attend: 路由帧 %s 失败 sid=%s: %s",
                    body.get("type"),
                    self._sid,
                    e,
                )

    def _route(self, body: dict):
        s = self._session
        t = body.get("type")
        if t == "attend_input":
            data = body.get("data", "")
            if data:
                s.write_input(data)
        elif t == "attend_key":
            key = body.get("key", "")
            if key:
                s.key_input(key, int(body.get("mods", 0) or 0))
        elif t == "attend_keyup":
            key = body.get("key", "")
            if key:
                s.key_up(key, int(body.get("mods", 0) or 0))
        elif t == "attend_mouse":
            s.mouse_input(
                int(body.get("x", 0)),
                int(body.get("y", 0)),
                body.get("kind", "press"),
                body.get("button", "left"),
                int(body.get("mods", 0) or 0),
            )
        elif t == "attend_resize":
            if not self._is_sub:
                cols = int(body.get("cols", 0))
                rows = int(body.get("rows", 0))
                if cols > 0 and rows > 0:
                    s.resize(cols, rows)
                    # ConPTY repaint 字节经队列自然流出；置事件加速推送
                    self._screen_event.set()
        else:
            _logger.debug("attend: 忽略未知帧 %s", t)
