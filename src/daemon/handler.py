"""请求处理器 — RequestHandler

处理单个请求消息的派发与业务逻辑。
每条命令对应一个 _handle_* 方法，新增命令时在此添加。
不再依赖 socket 连接，输入为请求 dict，输出为响应 dict。

result 响应格式（v4 规范化）:
    {
        "type": "result",
        "session_id": "xxx",
        "output_offset": N,
        "output": "...",
        "trigger_matched": bool,
        "reason": str,
        "program": {"running": bool, "exit_code": int/None, ...},
        "debug": {"processes": [...], "gui_windows": [...], "pending_events": [...]}
    }
"""

import re
import json
import time
import logging
import threading
import traceback
from typing import Optional

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

_logger = logging.getLogger("pty-daemon")


def _validate_field(value, name: str, max_len: int) -> dict:
    """验证请求字段长度，超限时返回错误响应

    Args:
        value:   待验证的字段值。
        name:    字段名称。
        max_len: 最大允许长度。

    Returns:
        error dict 或 None（通过验证）。
    """
    if isinstance(value, str) and len(value) > max_len:
        return {
            "type": "error",
            "error": f"参数 '{name}' 过长（最多 {max_len} 字符）",
        }
    return None


class RequestHandler:
    """处理单个请求消息

    从请求 dict 中解析指令类型，分发到对应的处理方法。
    每条 _handle_* 方法完成业务逻辑后返回响应 dict。

    认证：支持多令牌验证（当前令牌 + 宽限期旧令牌）。
    """

    def __init__(self, manager: SessionManager, auth_token: str = "", server=None):
        self.manager = manager
        self._server = server  # DaemonServer 实例，用于 stop 优雅关闭
        self._lock = threading.Lock()
        self._auth_enforced = bool(auth_token)
        self._auth_tokens: dict = (
            {auth_token: float("inf")} if auth_token else {}
        )

    def add_valid_token(self, new_token: str, old_token: str):
        """添加新令牌，旧令牌指定宽限期截止时间"""
        now = time.monotonic()
        with self._lock:
            self._auth_tokens[new_token] = float("inf")
            if old_token:
                self._auth_tokens[old_token] = now + AUTH_TOKEN_GRACE_PERIOD

    def _is_token_valid(self, token: str) -> bool:
        """验证令牌是否有效（惰性清理过期令牌）"""
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

    def handle(self, msg: dict) -> dict:
        """处理一个请求，返回响应 dict

        Args:
            msg: 请求消息字典。

        Returns:
            响应字典（type: result/ok/error/pong 等）。
        """
        try:
            msg_type = msg.get("type", "")
            session_id = msg.get("id", "")
            detail = self._get_detail(msg)

            # 认证验证：ping/stop 不需要 token
            if msg_type not in ("ping", "stop") and self._auth_enforced:
                if not self._is_token_valid(msg.get("token", "")):
                    _logger.warning("认证失败: type=%s id=%s", msg_type, session_id)
                    return {"type": "error", "error": "认证失败"}

            _logger.info("请求: %s id=%s %s", msg_type, session_id, detail)

            if msg_type == "ping":
                return {"type": "pong"}
            elif msg_type == "exec":
                return self._handle_exec(msg)
            elif msg_type == "send":
                return self._handle_send(msg)
            elif msg_type == "read":
                return self._handle_read(msg)
            elif msg_type == "list":
                sessions = self.manager.list_sessions()
                for s in sessions:
                    sid = s.get("id", "")
                    session = self.manager.get_session(sid)
                    s["pending_events"] = session.pending_event_count if session else 0
                return {"type": "ok", "sessions": sessions}
            elif msg_type == "kill":
                return self._handle_kill(msg)
            elif msg_type == "events":
                return self._handle_events(msg)
            elif msg_type == "closewin":
                return self._handle_closewin(msg)
            elif msg_type == "stop":
                return {"type": "ok"}
            else:
                return {"type": "error", "error": f"未知指令类型: {msg_type}"}

        except json.JSONDecodeError:
            _logger.error("JSON 解析失败")
            return {"type": "error", "error": "请求格式错误: JSON 解析失败"}
        except Exception as e:
            tb = traceback.format_exc()
            _logger.error("请求处理异常: %s", e)
            _logger.error(tb)
            return {"type": "error", "error": "服务器内部错误"}

    @staticmethod
    def _format_iso_ms(timestamp: float) -> str:
        """将 Unix 时间戳转为 ISO 8601 格式（两位毫秒）"""
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
        """构建规范化的 result 响应（v4）"""
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
        """按请求中的 keep_ansi 开关过滤 ANSI 颜色/样式码"""
        if not msg.get("keep_ansi"):
            return strip_ansi(output)
        return output

    def _run_trigger_flow(
        self,
        session,
        msg: dict,
        trigger_offset: int,
        trigger: str,
        newline: bool,
        fresh: bool,
        timeout: float,
        start_offset=None,
        result_type: str = "exec",
    ) -> dict:
        """执行 设置触发→等待→输出→响应 通用流程"""
        idle_timeout = msg.get("idle_timeout")
        idle_after_first = msg.get("idle_after_first_output", False)
        session.set_trigger(trigger, newline=newline, fresh=fresh,
                            start_offset=start_offset,
                            idle_timeout=idle_timeout,
                            idle_after_first_output=idle_after_first)
        matched, reason = session.wait_for_trigger(timeout, gui_short_circuit=False)
        output = session.get_output(from_offset=trigger_offset, encoding=msg.get("encoding"))
        output = self._strip_if_needed(output, msg)
        result = self._build_result(
            session.id, output, matched, reason,
            consume_events=True,
            has_trigger=True,
            result_type=result_type,
        )
        session.clear_trigger()
        return result

    def _run_no_trigger_flow(self, session, msg: dict,
                             result_type: str = "exec") -> dict:
        """执行 等待初始输出→输出→响应 通用流程（无触发条件）"""
        idle_timeout = msg.get("idle_timeout")
        idle_after_first = msg.get("idle_after_first_output", False)

        session.wait_for_initial_output(timeout=0.5)

        if idle_timeout is not None:
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
        return self._build_result(
            session.id, output, matched, reason,
            consume_events=True,
            has_trigger=False,
            result_type=result_type,
        )

    def _validate_request(self, msg: dict, fields: list) -> dict:
        """批量验证请求字段长度

        Args:
            msg:    请求消息。
            fields: (field_name, max_len) 元组列表。

        Returns:
            error dict 或 None（全部通过）。
        """
        for name, max_len in fields:
            err = _validate_field(msg.get(name), name, max_len)
            if err:
                return err
        return None

    def _handle_exec(self, msg: dict) -> dict:
        """处理 exec 指令：启动新会话并等待触发"""
        session_id = msg.get("id", "")
        command = msg.get("command")
        trigger = msg.get("trigger")

        err = self._validate_request(msg, [
            ("id", MAX_SESSION_ID_LEN),
            ("command", MAX_COMMAND_LEN),
            ("trigger", MAX_PATTERN_LEN),
        ])
        if err:
            return err

        _logger.info("_handle_exec: id=%r cmd=%r trigger=%r timeout=%r "
                     "idle_timeout=%r",
                     session_id,
                     command[:200] if isinstance(command, str) else command,
                     trigger, msg.get("timeout"), msg.get("idle_timeout"))

        if not session_id:
            return {"type": "error", "error": "缺少会话 id"}
        if not command:
            return {"type": "error", "error": "缺少 command 参数"}

        existing = self.manager.get_session(session_id)
        if existing:
            if not existing.running:
                return {
                    "type": "error",
                    "error": f"会话 '{session_id}' 已结束，请先 kill 后重新 exec",
                }
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
                return {"type": "error", "error": f"会话 '{session_id}' 已存在"}
            except Exception as e:
                _logger.error("会话 '%s' 启动失败: %s", session_id, e, exc_info=True)
                return {"type": "error", "error": "启动会话失败"}

        if trigger:
            trigger_offset = 0 if msg.get("full") else session.output_offset
            start_offset = 0 if not existing else None
            return self._run_trigger_flow(
                session, msg, trigger_offset,
                trigger, msg.get("newline", False),
                msg.get("fresh", False), msg.get("timeout", 120),
                start_offset=start_offset,
                result_type="exec",
            )
        else:
            return self._run_no_trigger_flow(session, msg, result_type="exec")

    def _handle_send(self, msg: dict) -> dict:
        """处理 send 指令：向运行中的会话发送输入并等待触发"""
        session_id = msg.get("id", "")
        input_text = msg.get("input", "")
        trigger = msg.get("trigger")

        err = self._validate_request(msg, [
            ("id", MAX_SESSION_ID_LEN),
            ("input", MAX_INPUT_LEN),
            ("trigger", MAX_PATTERN_LEN),
        ])
        if err:
            return err

        if not session_id:
            return {"type": "error", "error": "缺少会话 id"}

        session = self.manager.get_session(session_id)
        if not session:
            return {
                "type": "error",
                "error": f"会话 '{session_id}' 不存在",
                "suggest": "使用 'app.py list' 查看可用会话",
            }

        if not session.running:
            output = session.get_output(encoding=msg.get("encoding"))
            output = self._strip_if_needed(output, msg)
            return self._build_result(
                session_id, output, False, "ended",
                consume_events=True,
                has_trigger=bool(trigger),
                result_type="send",
                warning="会话已结束（旧会话数据）",
            )

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
            _logger.error("会话 '%s' 写入失败: %s", session_id, e, exc_info=True)
            return {"type": "error", "error": "写入输入失败"}

        if trigger:
            return self._run_trigger_flow(
                session, msg, trigger_offset,
                trigger, msg.get("newline", False),
                msg.get("fresh", False), msg.get("timeout", 120),
                result_type="send",
            )
        else:
            return self._run_no_trigger_flow(session, msg, result_type="send")

    def _handle_read(self, msg: dict) -> dict:
        """处理 read 指令：直接读取会话终端输出"""
        session_id = msg.get("id", "")
        lines_param = msg.get("lines")
        grep = msg.get("grep")
        offset = msg.get("offset")
        encoding = msg.get("encoding")

        err = self._validate_request(msg, [
            ("id", MAX_SESSION_ID_LEN),
            ("grep", MAX_PATTERN_LEN),
        ])
        if err:
            return err

        if not session_id:
            return {"type": "error", "error": "缺少会话 id"}

        session = self.manager.get_session(session_id)
        if not session:
            return {"type": "error", "error": f"会话 '{session_id}' 不存在"}

        ended_warning = "会话已结束（旧会话数据）" if not session.running else None

        read_offset = offset
        if msg.get("full"):
            read_offset = 0

        output = session.get_output(from_offset=read_offset, encoding=encoding)
        output = self._strip_if_needed(output, msg)

        if read_offset is not None and not lines_param and not grep:
            return self._build_result(
                session_id, output, False, "ok",
                has_trigger=False, result_type="read",
                warning=ended_warning,
            )

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
                    return {"type": "error", "error": f"无效的行范围: {lines_param}"}
            else:
                try:
                    n = int(lines_param)
                    lines = lines[-n:] if n > 0 else []
                except ValueError:
                    return {"type": "error", "error": f"无效的 lines 参数: {lines_param}"}

        if grep:
            try:
                pat = re.compile(grep)
                lines = [l for l in lines if safe_regex_search(pat, l)]
            except re.error:
                return {"type": "error", "error": f"无效的正则表达式: {grep}"}

        output = "\n".join(lines)
        return self._build_result(
            session_id, output, False, "ok",
            has_trigger=False, result_type="read",
            warning=ended_warning,
        )

    def _handle_kill(self, msg: dict) -> dict:
        """处理 kill 指令：终止指定会话"""
        session_id = msg.get("id", "")
        _logger.info("_handle_kill: id=%r", session_id)
        if not session_id:
            return {"type": "error", "error": "缺少会话 id"}
        session = self.manager.get_session(session_id)
        if not session:
            return {"type": "error", "error": f"会话 '{session_id}' 不存在"}
        try:
            self.manager.remove_session(session_id)
            _logger.info("会话 '%s' 已终止", session_id)
        except Exception:
            _logger.warning("终止会话 '%s' 时发生异常", session_id, exc_info=True)
        return {"type": "ok", "note": f"会话 {session_id} 已终止"}

    def _handle_closewin(self, msg: dict) -> dict:
        """处理 closewin 指令：关闭指定 GUI 窗口"""
        session_id = msg.get("id", "")
        hwnd = msg.get("hwnd")
        if not session_id:
            return {"type": "error", "error": "缺少会话 id"}
        if hwnd is None:
            return {"type": "error", "error": "缺少 hwnd 参数"}
        session = self.manager.get_session(session_id)
        if not session:
            return {"type": "error", "error": f"会话 '{session_id}' 不存在"}
        try:
            ok = session.close_window(hwnd)
            return {"type": "ok", "closed": ok, "hwnd": hwnd}
        except Exception as e:
            _logger.warning("关闭窗口异常: %s", e)
            return {"type": "error", "error": "关闭窗口失败"}

    def _handle_events(self, msg: dict) -> dict:
        """处理 events 指令：获取会话的所有事件"""
        session_id = msg.get("id", "")
        if not session_id:
            return {"type": "error", "error": "缺少会话 id"}
        session = self.manager.get_session(session_id)
        if not session:
            return {"type": "error", "error": f"会话 '{session_id}' 不存在"}
        last_n = msg.get("last")
        since = msg.get("since")
        until = msg.get("until")
        _logger.info("_handle_events: id=%r last=%s since=%s until=%s",
                     session_id, last_n, since, until)
        events = session.get_all_events(last=last_n, since=since, until=until)
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
        return resp