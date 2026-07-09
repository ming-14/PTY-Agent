"""请求处理器 — RequestHandler

处理单个客户端连接的消息派发与业务逻辑。
每条命令对应一个 _handle_* 方法，新增命令时在此添加。

result 响应格式（v2 规范化）:
    {
        "type": "result",
        "session_id": "xxx",
        "output_offset": N,
        "output": "...",
        "trigger": {"matched": bool, "reason": str},
        "program": {"running": bool, "exit_code": int/None, "error_message": str/None, "pty_type": str},
        "debug": {
            "processes": [int, ...],
            "gui_windows": [{"hwnd": int, "pid": int, "title": str, "class_name": str}, ...]
        }
    }
"""

import re
import json
import socket
import time
import logging
import threading
import traceback
from typing import Optional

from ..protocol.message import Message
from ..protocol.ansi import strip_ansi
from ..session.manager import SessionManager
from ..session.output import safe_regex_search
from ..config import (
    MAX_SESSION_ID_LEN,
    MAX_COMMAND_LEN,
    MAX_PATTERN_LEN,
    MAX_INPUT_LEN,
    AUTH_TOKEN_GRACE_PERIOD,
)

def _validate_field(value, name: str, max_len: int, conn) -> bool:
    """验证请求字段长度，超限时返回错误并断开

    Args:
        value:   待验证的字段值。
        name:    字段名称（用于错误消息）。
        max_len: 最大允许长度。
        conn:    TCP 连接。

    Returns:
        True 通过验证，False 已发送错误响应。
    """
    if isinstance(value, str) and len(value) > max_len:
        Message.send(conn, {
            "type": "error",
            "error": f"参数 '{name}' 过长（最多 {max_len} 字符）",
        })
        return False
    return True

_logger = logging.getLogger("pty-daemon")


class RequestHandler:
    """处理单个客户端连接请求

    从 TCP 连接接收 JSON 消息，解析指令类型，分发到对应的处理方法。
    每条 _handle_* 方法完成业务逻辑后调用 Message.send 返回响应。

    认证：支持多令牌验证（当前令牌 + 宽限期旧令牌）。
    """

    def __init__(self, manager: SessionManager, auth_token: str = "", server=None):
        self.manager = manager
        self._server = server  # DaemonServer 实例，用于 stop 优雅关闭
        self._lock = threading.Lock()
        # 显式标志位：auth_token 非空时启用认证
        # 不使用空字典 self._auth_tokens 的 falsy 判断，防止意外绕过
        self._auth_enforced = bool(auth_token)
        # token → 过期截止时间（monotonic）。空令牌表示认证未启用。
        self._auth_tokens: dict = (
            {auth_token: float("inf")} if auth_token else {}
        )

    def add_valid_token(self, new_token: str, old_token: str):
        """添加新令牌，旧令牌指定宽限期截止时间

        使用绝对时间戳而非 Timer 定时移除，避免定时器偏差导致
        在宽限期边界到达的请求被错误拒绝。

        Args:
            new_token: 新生成的令牌（无过期，有效期至守护进程结束）。
            old_token: 需要保留宽限期的旧令牌。
        """
        now = time.monotonic()
        with self._lock:
            self._auth_tokens[new_token] = float("inf")
            if old_token:
                self._auth_tokens[old_token] = now + AUTH_TOKEN_GRACE_PERIOD

    def _is_token_valid(self, token: str) -> bool:
        """验证令牌是否有效（不考虑过期则清理）

        同时惰性清理已过期的令牌，避免内存泄漏。
        """
        now = time.monotonic()
        with self._lock:
            deadline = self._auth_tokens.get(token)
            if deadline is None:
                return False
            if deadline <= now:
                self._auth_tokens.pop(token, None)
                return False
            return True

    def _get_detail(self, msg: dict) -> str:
        """从请求消息中提取描述性字段"""
        parts = []
        if msg.get("command"):
            cmd = str(msg["command"])
            parts.append(f"cmd={cmd[:60]!r}")
        if msg.get("trigger"):
            parts.append(f"trigger={msg['trigger']!r}")
        if msg.get("encoding"):
            parts.append(f"enc={msg['encoding']!r}")
        if msg.get("offset"):
            parts.append(f"offset={msg['offset']}")
        return ", ".join(parts) if parts else ""

    def handle(self, conn, addr):
        """处理一个客户端连接

        主派发方法：解析消息 → 按 type 分配 → 返回响应。

        Args:
            conn: 已连接的 TCP socket。
            addr: 客户端地址。
        """
        try:
            msg = Message.recv(conn)
            if msg is None:
                return

            msg_type = msg.get("type", "")
            session_id = msg.get("id", "")
            detail = self._get_detail(msg)

            # 认证验证：ping 不需要 token（服务发现用），stop 不需要 token（本机已有 kill 权限），其余请求必须携带有效 token
            if msg_type not in ("ping", "stop") and self._auth_enforced:
                if not self._is_token_valid(msg.get("token", "")):
                    _logger.warning("认证失败: token 不匹配 (type=%s id=%s)", msg_type, session_id)
                    Message.send(conn, {"type": "error", "error": "认证失败"})
                    return

            _logger.info("请求: %s id=%s %s", msg_type, session_id, detail)

            if msg_type == "ping":
                Message.send(conn, {"type": "pong"})
            elif msg_type == "exec":
                self._handle_exec(conn, msg)
            elif msg_type == "send":
                self._handle_send(conn, msg)
            elif msg_type == "read":
                self._handle_read(conn, msg)
            elif msg_type == "list":
                sessions = self.manager.list_sessions()
                # 为每个会话添加待处理事件计数
                for s in sessions:
                    sid = s.get("id", "")
                    session = self.manager.get_session(sid)
                    s["pending_events"] = session.pending_event_count if session else 0
                Message.send(conn, {"type": "ok", "sessions": sessions})
            elif msg_type == "kill":
                self._handle_kill(conn, msg)
            elif msg_type == "events":
                self._handle_events(conn, msg)
            elif msg_type == "closewin":
                self._handle_closewin(conn, msg)
            elif msg_type == "stop":
                Message.send(conn, {"type": "ok"})
                _logger.info("收到停止命令，关闭守护进程...")
                try:
                    conn.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                conn.close()

                if self._server:
                    self._server.stop()
            else:
                err = f"未知指令类型: {msg_type}"
                _logger.warning(err)
                Message.send(conn, {"type": "error", "error": err})

        except json.JSONDecodeError:
            _logger.error("JSON 解析失败")
            try:
                Message.send(conn, {
                    "type": "error",
                    "error": "请求格式错误: JSON 解析失败",
                })
            except Exception:
                pass
        except (BrokenPipeError, ConnectionError, OSError) as e:
            _logger.warning("客户端连接异常: %s", e)
        except Exception as e:
            tb = traceback.format_exc()
            _logger.error("请求处理异常: %s", e)
            _logger.error(tb)
            try:
                Message.send(conn, {
                    "type": "error",
                    "error": "服务器内部错误",
                })
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _format_iso_ms(timestamp: float) -> str:
        """将 Unix 时间戳转为 ISO 8601 格式（两位毫秒）

        Args:
            timestamp: Unix 时间戳。

        Returns:
            ISO 8601 字符串，如 "2026-06-22T14:32:15.47"。
        """
        from datetime import datetime
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 10000:02d}"

    def _build_result(
        self,
        session_id: str,
        output: str,
        matched: bool,
        reason: str,
        consume_events: bool = False,
        has_trigger: bool = True,
        result_type: str = "result",
        warning: Optional[str] = None,
    ) -> dict:
        """构建规范化的 result 响应（v4）

        Args:
            session_id: 会话标识符。
            output:     终端输出文本。
            matched:    是否命中触发条件。
            reason:     结束原因（matched/timeout/idle_timeout/ended/gui_detected/ok）。
            consume_events: 是否消费并返回待处理事件。
            has_trigger: 是否有 trigger 参数（有则输出 trigger_matched）。
            result_type: 响应类型字段值（命令名：exec/send/read）。

        Returns:
            规范化的 result 字典。
        """
        session = self.manager.get_session(session_id)
        result: dict = {
            "type": result_type,
            "session_id": session_id,
            "output": output,
            "output_offset": session.output_offset if session else 0,
        }

        if has_trigger:
            result["trigger_matched"] = matched
        result["reason"] = reason

        program: dict = {
            "command": session.command if session else None,
            "running": session.running if session else False,
            "pty_type": session.pty_type if session else "none",
        }
        if session and session.start_time:
            program["start_time"] = self._format_iso_ms(session.start_time)
        exit_code = session.exit_code if session else None
        if exit_code is not None:
            program["exit_code"] = exit_code
        error_message = session.error_message if session else None
        if error_message is not None:
            program["error_message"] = error_message
        result["program"] = program

        if consume_events:
            processes = session.processes if session else []
            process_tree = []
            if processes:
                for pid in processes:
                    if pid == 0:
                        continue
                    try:
                        from ..session.process import _get_process_path
                        path = _get_process_path(pid)
                    except Exception:
                        path = f"PID {pid}"
                    process_tree.append({"pid": pid, "path": path})
            events = session.consume_events() if session else None
            if events:
                events = [e for e in events if e.get("pid", 0) != 0]

            debug: dict = {}
            if process_tree:
                debug["processes"] = process_tree
            gui_windows = session.gui_windows if session else None
            if gui_windows:
                debug["gui_windows"] = gui_windows
            if events:
                debug["pending_events"] = events
            if debug:
                result["debug"] = debug
        if warning:
            result["warning"] = warning
        return result

    def _strip_if_needed(self, output: str, msg: dict) -> str:
        """按请求中的 keep_ansi 开关过滤 ANSI 颜色/样式码

        仅过滤 SGR 颜色/样式（\\x1b[31m 等）和 OSC 窗口标题序列。
        清屏（\\x1b[2J）、光标定位（\\x1b[H）、清行（\\x1b[K）等控制序列始终保留，
        不受 keep_ansi 影响。
        """
        if not msg.get("keep_ansi"):
            return strip_ansi(output)
        return output

    def _run_trigger_flow(
        self,
        conn,
        session,
        msg: dict,
        trigger_offset: int,
        trigger: str,
        newline: bool,
        fresh: bool,
        timeout: float,
        start_offset=None,
        result_type: str = "exec",
    ):
        """执行 设置触发→等待→输出→响应 通用流程

        被 _handle_exec 和 _handle_send 复用。

        Args:
            start_offset: 触发扫描起始偏移。None 表示从当前缓冲区末尾开始。
                           _handle_exec 传入 0 以扫描初始输出。
        """
        idle_timeout = msg.get("idle_timeout")
        idle_after_first = msg.get("idle_after_first_output", False)
        session.set_trigger(trigger, newline=newline, fresh=fresh,
                            start_offset=start_offset,
                            idle_timeout=idle_timeout,
                            idle_after_first_output=idle_after_first)
        matched, reason = session.wait_for_trigger(timeout, gui_short_circuit=False)
        output = session.get_output(from_offset=trigger_offset, encoding=msg.get("encoding"))
        output = self._strip_if_needed(output, msg)
        Message.send(conn, self._build_result(
            session.id, output, matched, reason,
            consume_events=True,
            has_trigger=True,
            result_type=result_type,
        ))
        session.clear_trigger()

    def _run_no_trigger_flow(self, conn, session, msg: dict,
                             result_type: str = "exec"):
        """执行 等待初始输出→输出→响应 通用流程（无触发条件）

        如果请求中指定了 idle_timeout，则进入静默超时等待循环；
        否则仅等待短暂初始输出后返回。
        """
        idle_timeout = msg.get("idle_timeout")
        idle_after_first = msg.get("idle_after_first_output", False)

        # 先等待初始输出抵达
        session.wait_for_initial_output(timeout=0.5)

        if idle_timeout is not None:
            # 用永不匹配的正则 + idle_timeout，实现纯静默超时等待
            # 即使无 trigger，也能捕获进程结束和 GUI 窗口事件
            session.set_trigger(
                pattern=r"(?!x)x",
                newline=False,
                fresh=True,
                start_offset=session.output_offset,
                idle_timeout=idle_timeout,
                idle_after_first_output=idle_after_first,
            )
            matched, reason = session.wait_for_trigger(timeout=msg.get("timeout", 120))
            session.clear_trigger()
        else:
            matched, reason = False, "ok"

        output = session.get_output(encoding=msg.get("encoding"))
        output = self._strip_if_needed(output, msg)
        Message.send(conn, self._build_result(
            session.id, output, matched, reason,
            consume_events=True,
            has_trigger=False,
            result_type=result_type,
        ))

    # ---- 具体指令处理 ----

    def _validate_request(self, conn, msg: dict, fields: list) -> bool:
        """批量验证请求字段长度

        Args:
            conn:   TCP 连接。
            msg:    请求消息。
            fields: (field_name, max_len) 元组列表。

        Returns:
            True 全部通过。
        """
        for name, max_len in fields:
            if not _validate_field(msg.get(name), name, max_len, conn):
                return False
        return True

    def _handle_exec(self, conn, msg: dict):
        """处理 exec 指令：启动新会话并等待触发"""
        session_id = msg.get("id", "")
        command = msg.get("command")
        trigger = msg.get("trigger")
        # 输入长度验证
        if not self._validate_request(conn, msg, [
            ("id", MAX_SESSION_ID_LEN),
            ("command", MAX_COMMAND_LEN),
            ("trigger", MAX_PATTERN_LEN),
        ]):
            return
        _logger.info("_handle_exec: id=%r cmd=%r trigger=%r encoding=%r timeout=%r "
                     "idle_timeout=%r idle_after_first=%r",
                     session_id,
                     command[:200] if isinstance(command, str) else command,
                     trigger, msg.get("encoding"), msg.get("timeout"),
                     msg.get("idle_timeout"), msg.get("idle_after_first_output"))

        if not session_id:
            Message.send(conn, {"type": "error", "error": "缺少会话 id"})
            return
        if not command:
            Message.send(conn, {"type": "error", "error": "缺少 command 参数"})
            return

        existing = self.manager.get_session(session_id)
        if existing:
            if not existing.running:
                Message.send(conn, {
                    "type": "error",
                    "error": f"会话 '{session_id}' 已结束，请先 kill 后重新 exec",
                })
                return
            session = existing
            _logger.info("会话 '%s' 已存在，直接附加", session_id)
        else:
            try:
                session = self.manager.create_session(
                    session_id, command, encoding=msg.get("encoding"),
                    cwd=msg.get("cwd"),
                )
                log_cmd = (
                    command if isinstance(command, str)
                    else " ".join(command)
                )
                _logger.info("创建会话 '%s': %s", session_id, log_cmd)
            except KeyError:
                Message.send(conn, {
                    "type": "error",
                    "error": f"会话 '{session_id}' 已存在",
                })
                return
            except Exception as e:
                tb = traceback.format_exc()
                _logger.error("会话 '%s' 启动失败: %s", session_id, e)
                _logger.error(tb)
                Message.send(conn, {
                    "type": "error",
                    "error": "启动会话失败",
                })
                return

        if trigger:
            trigger_offset = 0 if msg.get("full") else session.output_offset
            start_offset = 0 if not existing else None
            self._run_trigger_flow(
                conn, session, msg, trigger_offset,
                trigger, msg.get("newline", False),
                msg.get("fresh", False), msg.get("timeout", 120),
                start_offset=start_offset,
                result_type="exec",
            )
        else:
            self._run_no_trigger_flow(conn, session, msg, result_type="exec")

    def _handle_send(self, conn, msg: dict):
        """处理 send 指令：向运行中的会话发送输入并等待触发"""
        session_id = msg.get("id", "")
        input_text = msg.get("input", "")
        trigger = msg.get("trigger")
        # 输入长度验证
        if not self._validate_request(conn, msg, [
            ("id", MAX_SESSION_ID_LEN),
            ("input", MAX_INPUT_LEN),
            ("trigger", MAX_PATTERN_LEN),
        ]):
            return

        if not session_id:
            Message.send(conn, {"type": "error", "error": "缺少会话 id"})
            return

        session = self.manager.get_session(session_id)
        if not session:
            Message.send(conn, {
                "type": "error",
                "error": f"会话 '{session_id}' 不存在",
                "suggest": "使用 'app.py list' 查看可用会话",
            })
            return

        if not session.running:
            output = session.get_output(encoding=msg.get("encoding"))
            output = self._strip_if_needed(output, msg)
            Message.send(conn, self._build_result(
                session_id, output, False, "ended",
                consume_events=True,
                has_trigger=bool(trigger),
                result_type="send",
                warning="会话已结束（旧会话数据）",
            ))
            return

        # 先设置触发条件，再写入输入
        # 避免 reader 线程在 write_input 后迅速读取响应数据追加到缓冲区，
        # 导致 set_trigger 的 _trigger_start_offset 被设在缓冲区末尾，
        # 触发扫描永远看不到响应数据（竞态条件）。
        # 注意：_run_trigger_flow 中会再次调用 set_trigger（含 idle_timeout 等参数），
        # 第一次调用仅用作竞态防护，实际参数以第二次为准。
        if trigger:
            trigger_offset = 0 if msg.get("full") else session.output_offset
            session.set_trigger(trigger, newline=msg.get("newline", False),
                                fresh=msg.get("fresh", False))
            _logger.info("send trigger: id=%r trigger=%r offset=%d bufsize=%d",
                         session_id, trigger, trigger_offset, session.output_offset)

        try:
            session.write_input(input_text)
            _logger.info("会话 '%s' 输入: %s", session_id, repr(input_text[:100]))
        except Exception as e:
            tb = traceback.format_exc()
            _logger.error("会话 '%s' 写入失败: %s", session_id, e)
            _logger.error(tb)
            Message.send(conn, {
                "type": "error",
                "error": "写入输入失败",
            })
            return

        if trigger:
            self._run_trigger_flow(
                conn, session, msg, trigger_offset,
                trigger, msg.get("newline", False),
                msg.get("fresh", False), msg.get("timeout", 120),
                result_type="send",
            )
        else:
            self._run_no_trigger_flow(conn, session, msg, result_type="send")

    def _handle_read(self, conn, msg: dict):
        """处理 read 指令：直接读取会话终端输出（不等待触发）"""
        session_id = msg.get("id", "")
        lines_param = msg.get("lines")
        grep = msg.get("grep")
        offset = msg.get("offset")
        encoding = msg.get("encoding")
        # 输入长度验证
        if not self._validate_request(conn, msg, [
            ("id", MAX_SESSION_ID_LEN),
            ("grep", MAX_PATTERN_LEN),
        ]):
            return

        if not session_id:
            Message.send(conn, {"type": "error", "error": "缺少会话 id"})
            return

        session = self.manager.get_session(session_id)
        if not session:
            Message.send(conn, {
                "type": "error",
                "error": f"会话 '{session_id}' 不存在",
            })
            return

        ended_warning = "会话已结束（旧会话数据）" if not session.running else None

        read_offset = offset
        if msg.get("full"):
            read_offset = 0

        output = session.get_output(from_offset=read_offset, encoding=encoding)
        output = self._strip_if_needed(output, msg)

        if read_offset is not None and not lines_param and not grep:
            Message.send(conn, self._build_result(
                session_id, output, False, "ok",
                has_trigger=False, result_type="read",
                warning=ended_warning,
            ))
            return

        lines = output.splitlines()

        if lines_param is not None:
            if isinstance(lines_param, int):
                lines = lines[-lines_param:] if lines_param > 0 else []
            elif isinstance(lines_param, str) and ":" in lines_param:
                parts = lines_param.split(":", 1)
                try:
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if parts[1] else len(lines)
                    lines = lines[start:end]
                except (ValueError, IndexError):
                    Message.send(conn, {
                        "type": "error",
                        "error": f"无效的行范围: {lines_param}",
                    })
                    return
            else:
                try:
                    n = int(lines_param)
                    lines = lines[-n:] if n > 0 else []
                except ValueError:
                    Message.send(conn, {
                        "type": "error",
                        "error": f"无效的 lines 参数: {lines_param}",
                    })
                    return

        if grep:
            try:
                pat = re.compile(grep)
                lines = [l for l in lines if safe_regex_search(pat, l)]
            except re.error:
                Message.send(conn, {
                    "type": "error",
                    "error": f"无效的正则表达式: {grep}",
                })
                return

        output = "\n".join(lines)
        Message.send(conn, self._build_result(
            session_id, output, False, "ok",
            has_trigger=False, result_type="read",
            warning=ended_warning,
        ))

    def _handle_kill(self, conn, msg: dict):
        """处理 kill 指令：终止指定会话"""
        session_id = msg.get("id", "")
        _logger.info("_handle_kill: id=%r", session_id)
        if not session_id:
            Message.send(conn, {"type": "error", "error": "缺少会话 id"})
            return
        try:
            self.manager.remove_session(session_id)
            _logger.info("会话 '%s' 已终止", session_id)
        except Exception:
            _logger.warning("终止会话 '%s' 时发生异常", session_id, exc_info=True)
        Message.send(conn, {"type": "ok"})

    def _handle_closewin(self, conn, msg: dict):
        """处理 closewin 指令：关闭指定 GUI 窗口

        请求格式:
            {"type": "closewin", "id": "<会话ID>", "hwnd": <窗口句柄>}
        """
        session_id = msg.get("id", "")
        hwnd = msg.get("hwnd")

        if not session_id:
            Message.send(conn, {"type": "error", "error": "缺少会话 id"})
            return
        if hwnd is None:
            Message.send(conn, {"type": "error", "error": "缺少 hwnd 参数"})
            return

        session = self.manager.get_session(session_id)
        if not session:
            Message.send(conn, {
                "type": "error",
                "error": f"会话 '{session_id}' 不存在",
            })
            return

        try:
            ok = session.close_window(hwnd)
            Message.send(conn, {
                "type": "ok",
                "closed": ok,
                "hwnd": hwnd,
            })
        except Exception as e:
            _logger.warning("关闭窗口异常: %s", e)
            Message.send(conn, {
                "type": "error",
                "error": "关闭窗口失败",
            })

    def _handle_events(self, conn, msg: dict):
        """处理 events 指令：获取会话的所有事件

        始终返回全量事件（历史 + 待处理），不支持仅待处理模式。
        可通过 last/since/until 过滤。

        请求格式:
            {"type": "events", "id": "<会话ID>",
             "last": <int>, "since": <float>, "until": <float>}

        Args:
            conn: TCP 连接。
            msg:  请求消息。
        """
        session_id = msg.get("id", "")

        if not session_id:
            Message.send(conn, {"type": "error", "error": "缺少会话 id"})
            return
        session = self.manager.get_session(session_id)
        if not session:
            Message.send(conn, {
                "type": "error",
                "error": f"会话 '{session_id}' 不存在",
            })
            return

        # 提取过滤参数
        last_n = msg.get("last")
        since = msg.get("since")   # Unix 时间戳 float
        until = msg.get("until")   # Unix 时间戳 float

        _logger.info("_handle_events: id=%r last=%s since=%s until=%s "
                     "pending=%d history=%d",
                     session_id, last_n, since, until,
                     session.pending_event_count,
                     session.event_history.history_count)

        # 始终返回所有事件（历史 + 待处理，不消费）
        events = session.get_all_events(last=last_n, since=since, until=until)

        # 对每个事件执行存在性检测，添加 still_active 字段
        for ev in events:
            ev["still_active"] = session.check_event_existence(ev)

        resp = {
            "type": "ok",
            "session_id": session_id,
            "pending_events": events,
            "count": len(events),
        }
        if not session.running:
            resp["warning"] = "会话已结束（旧会话数据）"
        Message.send(conn, resp)
