"""通知管理器 — NotificationManager（daemon 单例，与 workflow_manager 并列）

--notify 功能的后端存储：后台通知线程把"命令原本的返回"（完整命令响应 +
返回原因）发布到本管理器；wait 命令消费待消费通知摘要（移入归档）；
notice {nid} 查询完整响应（待消费队列 + 已消费归档均可查）。

设计：
- 全局 FIFO（deque，按发布时间序）+ 每会话计数上限（MAX_NOTIF_PER_SESSION，
  超限淘汰该会话最旧一条），总量自然受 MAX_SESSIONS × 每会话上限约束。
- 消费语义：wait 消费（consume_pending）与操作会话自动消费
  （consume_by_session）都把通知移入归档（上限 200 条淘汰最旧），
  不删除——notice 仍可查看已消费通知的完整内容。
- 自管道用 socket.socketpair() 而非 os.pipe()：wait handler 用 select 同时
  监听客户端连接与唤醒通道，os.pipe 在 Windows 上不能 select
  （WinError 10038，select 只收 socket），socketpair 两平台统一。
- publish 时写 1 字节唤醒字节（非阻塞，失败静默），wait 的 select 立即返回。
- 通知 nid 用 uuid4().hex，wait 返回摘要、notice 取完整。
- 队列仅存内存（daemon 重启即清空，与 set-default 全局默认一致）。

线程安全：单把 threading.Lock 保护队列与计数（daemon 多 Listener/多线程）。
"""

import collections
import socket
import threading
import time
import uuid
from datetime import datetime, timezone

from ..logging import get_logger
from ..protocol.reasons import Reason

_logger = get_logger("pty-daemon")


def _now_iso() -> str:
    """当前时间 ISO 8601（本地时区，毫秒），与事件历史时间格式一致"""
    dt = datetime.now(timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 10000:02d}"


# ── 通知详情映射 ──────────────────────────────────────────

def _default_notify_detail(response: dict) -> str:
    """命令类通知的默认详情文案（按 triggerReturnReason 映射）

    发布方已在 response 中自带 detail 时（如 subagent 插件），
    本函数不会被调用。仅对命令类通知（exec/send/read/mouse）生效。

    映射规则（与用户对齐的"按原因区分"方案）：
        trigger_matched  / 未知 → {sid}已完成
        trigger_timeout          → {sid}超时
        idle_timeout             → {sid}空闲超时
        program_ended            → {sid}已结束
        program_crashed          → {sid}已崩溃
        gui_detected             → {sid}检测到窗口
        cancelled                → {sid}已取消
    """
    sid = response.get("sessionId", "?")
    reason = response.get("triggerReturnReason", "")

    _detail_map = {
        "trigger_matched": f"{sid}已完成",
        "ok": f"{sid}已完成",
        "matched": f"{sid}已完成",
        "trigger_timeout": f"{sid}超时",
        "timeout": f"{sid}超时",
        "idle_timeout": f"{sid}空闲超时",
        "program_ended": f"{sid}已结束",
        "ended": f"{sid}已结束",
        "program_crashed": f"{sid}已崩溃",
        "crashed": f"{sid}已崩溃",
        "gui_detected": f"{sid}检测到窗口",
        "cancelled": f"{sid}已取消",
    }
    return _detail_map.get(reason, f"{sid}已完成")


class NotificationManager:
    """全局通知队列（线程安全，每会话 FIFO 上限淘汰）"""

    def __init__(self, max_per_session: int = 50):
        self._max_per_session = max_per_session
        self._lock = threading.Lock()
        # 全局 FIFO：元素 {nid, sessionId, createdAt, response}
        self._queue: collections.deque = collections.deque()
        # 已消费通知归档（wait/自动消费移入，notice 仍可查，上限淘汰最旧）
        self._archive: collections.deque = collections.deque(maxlen=200)
        # 会话 → 在队列中的条数（淘汰用）
        self._session_count: dict = {}
        # 自管道：socketpair 两平台可 select（os.pipe 在 Windows 不能 select）
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._closed = False

    # ── 发布 ──────────────────────────────────────────────

    def publish(self, response: dict) -> str:
        """发布一条通知（后台通知线程调用），返回通知 nid

        完整响应（build_result 产物，含 triggerReturnReason/outputStream 等）
        整体存入队列，供 notice {nid} 完整查看。

        摘要详情：发布方可在 response 中带 detail 字段自定义；命令类通知
        未带时按 triggerReturnReason 自动映射（_default_notify_detail）。
        """
        nid = uuid.uuid4().hex
        session_id = response.get("sessionId", "")
        entry = {
            "nid": nid,
            "sessionId": session_id,
            "createdAt": _now_iso(),
            "detail": response.get("detail") or _default_notify_detail(response),
            "response": response,
        }
        with self._lock:
            if session_id:
                count = self._session_count.get(session_id, 0) + 1
                self._session_count[session_id] = count
                if count > self._max_per_session:
                    # 该会话超上限：从最旧处淘汰其最早一条（只删本会话的）
                    for i, e in enumerate(self._queue):
                        if e["sessionId"] == session_id:
                            del self._queue[i]
                            self._session_count[session_id] = count - 1
                            break
            self._queue.append(entry)
        self._wake()
        _logger.info(
            "通知发布 nid=%s session=%s reason=%r queue=%d",
            nid,
            session_id,
            response.get("triggerReturnReason"),
            len(self._queue),
        )
        return nid

    # ── 查询（只读，不消费） ──────────────────────────────

    def pending_list(self) -> list:
        """全部待消费通知摘要 {nid, sessionId, detail, triggerReturnReason, createdAt}"""
        with self._lock:
            return [
                {
                    "nid": e["nid"],
                    "sessionId": e["sessionId"],
                    "detail": e.get("detail", ""),
                    "triggerReturnReason": e["response"].get("triggerReturnReason", ""),
                    "createdAt": e["createdAt"],
                }
                for e in self._queue
            ]

    def consume_pending(self) -> list:
        """返回全部待消费通知摘要并移入归档（wait 命令消费语义）

        通知移入归档而非删除：notice {nid} 仍可查看完整内容。
        消费后清空唤醒管道残留字节（避免下次 wait 的 select 被陈旧唤醒字节
        立即触发而空返回）。
        """
        with self._lock:
            result = [
                {
                    "nid": e["nid"],
                    "sessionId": e["sessionId"],
                    "detail": e.get("detail", ""),
                    "triggerReturnReason": e["response"].get("triggerReturnReason", ""),
                    "createdAt": e["createdAt"],
                }
                for e in self._queue
            ]
            for e in self._queue:
                self._archive.append(e)
            self._queue.clear()
            self._session_count.clear()
        self.drain()
        _logger.info("通知已消费 %d 条（移入归档，队列清空）", len(result))
        return result

    def consume_by_session(self, session_id: str) -> int:
        """消费某会话的所有通知并移入归档，返回消费数量（操作该会话后自动消费）"""
        if not session_id:
            return 0
        with self._lock:
            removed = [e for e in self._queue if e["sessionId"] == session_id]
            if not removed:
                return 0
            kept = collections.deque()
            for e in self._queue:
                if e["sessionId"] == session_id:
                    self._archive.append(e)
                else:
                    kept.append(e)
            self._queue = kept
            self._session_count.pop(session_id, None)
        self.drain()
        _logger.info("通知自动消费 session=%s %d 条（移入归档）", session_id, len(removed))
        return len(removed)

    def get_by_nid(self, nid: str):
        """按 nid 取完整响应（先查待消费队列，再查已消费归档）；不存在返回 None"""
        with self._lock:
            for e in self._queue:
                if e["nid"] == nid:
                    return e["response"]
            for e in self._archive:
                if e["nid"] == nid:
                    return e["response"]
        return None

    def pending_count(self) -> int:
        """当前待消费通知总数"""
        with self._lock:
            return len(self._queue)

    # ── wait 唤醒（自管道） ───────────────────────────────

    @property
    def wake_fd(self) -> int:
        """唤醒通道读端 fd（wait handler 用 select 监听）"""
        return self._wake_r.fileno()

    def drain(self) -> None:
        """清空唤醒通道已读字节（wait 被唤醒后调用）"""
        try:
            while self._wake_r.recv(65536):
                pass
        except (BlockingIOError, OSError):
            pass

    def _wake(self) -> None:
        """写 1 字节唤醒等待中的 wait handler（非阻塞，失败静默）"""
        try:
            self._wake_w.send(b"x")
        except (BlockingIOError, OSError):
            pass

    # ── 生命周期 ──────────────────────────────────────────

    def close(self) -> None:
        """关闭唤醒通道（daemon 停止时调用）"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._wake_r.close()
        except OSError:
            pass
        try:
            self._wake_w.close()
        except OSError:
            pass
        _logger.info("通知管理器已关闭（遗留 %d 条通知已丢弃）", len(self._queue))


# ── 后台通知线程 ──────────────────────────────────────────

def build_notify_waiting_response(ctx, session, msg, result_type: str) -> dict:
    """构造 --notify 立即返回的响应（reason=notify_waiting）

    与正常命令返回同结构（build_result），但不等待，取当前快照/增量后立即返回。
    """
    from ..execution.response import build_result, describe_output_format
    from ..execution.filtering import strip_if_needed

    cond = msg
    keep_ansi = cond.get("keep_ansi", False)
    is_sub = getattr(session, "mode", "pty") == "subprocess"
    if is_sub:
        output, _ = session.get_output_with_offset(
            from_offset=session.read_base(cond.get("full", False)),
            encoding=cond.get("encoding"),
        )
    else:
        output = session.get_snapshot(keep_ansi=keep_ansi)
        if not keep_ansi:
            output = strip_if_needed(output, cond)
    result = build_result(
        ctx.manager,
        session.id,
        output,
        False,
        Reason.NOTIFY_WAITING,
        consume_events=False,
        result_type=result_type,
        session=session,
        t_start=cond.get("_t_start"),
    )
    result["format"] = describe_output_format(cond, is_subprocess=is_sub)
    return result


def _notify_worker_run(ctx, session, msg, result_type, existing=False, extra_fields=None):
    """后台通知线程主函数：运行完整等待流程，结果发布为通知

    msg 携带原始的 trigger/timeout/idle 条件（与普通命令完全一致），
    _run_snapshot_flow 等原样执行等待逻辑，返回 reason 与完整响应。
    线程持有 session.hold() 避免会话结束期间组件被提前释放。
    """
    from ..execution import (
        _run_snapshot_flow,
        _run_subprocess_no_trigger_flow,
        _run_subprocess_trigger_flow,
    )
    from ..logging import get_logger as _get_logger

    _wlog = _get_logger("pty-daemon")
    # 通知线程的 elapsed 从线程启动时算起
    worker_msg = dict(msg)
    worker_msg["_t_start"] = time.monotonic()

    try:
        with session.hold():
            is_sub = getattr(session, "mode", "pty") == "subprocess"
            if is_sub:
                trigger = worker_msg.get("trigger")
                if trigger:
                    result, _ = _run_subprocess_trigger_flow(
                        ctx, None, session, worker_msg,
                        session.read_base(worker_msg.get("full", False)),
                        trigger,
                        worker_msg.get("newline", False),
                        worker_msg.get("fresh", False),
                        worker_msg.get("timeout", 120),
                        start_offset=0 if not existing else None,
                        result_type=result_type,
                        send_response=False,
                        apply_filter=False,
                    )
                else:
                    result, _ = _run_subprocess_no_trigger_flow(
                        ctx, None, session, worker_msg,
                        result_type=result_type,
                        send_response=False,
                        apply_filter=False,
                    )
            else:
                result, _ = _run_snapshot_flow(
                    ctx, None, session, worker_msg,
                    result_type=result_type,
                    send_response=False,
                    apply_filter=False,
                )
            if extra_fields:
                result.update(extra_fields)
            # 发布通知
            notify_mgr = getattr(getattr(ctx, "server", None), "notify_manager", None)
            if notify_mgr is not None:
                notify_mgr.publish(result)
    except Exception:
        _wlog.exception(
            "通知线程异常: session=%s type=%s", getattr(session, "id", "?"), result_type
        )


def spawn_notify_worker(ctx, session, msg, result_type: str, existing: bool = False,
                        extra_fields: dict = None):
    """启动后台通知线程（立即返回，不阻塞 handler）

    Args:
        ctx: HandlerContext
        session: Session 实例
        msg: 原始请求 msg dict（含 trigger/timeout/idle 等条件）
        result_type: 命令类型（exec/send/read/mouse）
        existing: exec 是否附加已有会话（影响子进程 start_offset）
        extra_fields: 额外字段合并到通知响应（mouse 的 performed 等）
    """
    import threading as _threading

    t = _threading.Thread(
        target=_notify_worker_run,
        args=(ctx, session, msg, result_type, existing, extra_fields),
        daemon=True,
        name="pty-notify-%s" % session.id,
    )
    t.start()
    return t
