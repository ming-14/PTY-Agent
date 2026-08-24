"""统一响应构造器

所有面向用户的输出（错误、警告、信息、命令结果）都通过此类构造，
确保字段命名和结构在 TCP handler / WebSocket handler / CLI 之间一致。

字段命名约定：
- 错误描述统一使用 "message" 字段（不再使用 "error" 字段）
- TCP 成功响应保留 "commandType" 字段
- WS 成功响应保留 "type" 字段
"""

from typing import Optional


class Response:
    """统一响应构造器 — 只负责构造 dict，不负责发送"""

    # ════════════════════════════════════════════════════════════
    #  通用消息（CLI + TCP + WS 共用）
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def error(message: str, **extra) -> dict:
        """错误响应

        Args:
            message: 错误描述。
            **extra: 额外字段（如 code / params / suggest）。
        """
        return {"type": "error", "message": message, **extra}

    @staticmethod
    def ws_error(message: str, code=None, params=None) -> dict:
        """WebSocket 错误响应（带 i18n 错误码）。

        Web 前端根据 code 映射本地文案；message 保留给非网页端消费者读取。

        Args:
            message: 错误描述（后端语言无关，通常为英文或空）。
            code:    i18n 错误码，前端字典 key。
            params:  插值参数（如 {error: <原始异常>}）。
        """
        resp = {"type": "error", "message": message}
        if code:
            resp["code"] = code
        if params:
            resp["params"] = params
        return resp

    @staticmethod
    def warning(message: str) -> dict:
        """警告响应"""
        return {"type": "warning", "message": message}

    @staticmethod
    def info(message: str) -> dict:
        """信息响应"""
        return {"type": "info", "message": message}

    @staticmethod
    def pong() -> dict:
        """Pong 响应"""
        return {"type": "pong"}

    @staticmethod
    def config(content: str) -> dict:
        """配置内容响应"""
        return {"type": "config", "content": content}

    # ════════════════════════════════════════════════════════════
    #  TCP 命令响应（使用 commandType）
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def command_result(command_type: str, session_id: str, **fields) -> dict:
        """TCP 命令结果骨架

        Args:
            command_type: 命令类型（exec/send/read/mouse/kill/stop/list/events/closewin）。
            session_id:   会话 ID。
            **fields:     额外字段。
        """
        result = {"commandType": command_type}
        if session_id is not None:
            result["sessionId"] = session_id
        result.update(fields)
        return result

    @staticmethod
    def wait_result(timeout: float, elapsed: float) -> dict:
        """Wait 命令响应"""
        return {"type": "wait", "timeout": timeout, "elapsed": elapsed}

    @staticmethod
    def debug_information(
        processes=None,
        gui_windows=None,
        pending_events=None,
        hint=None,
        elapsed_ms=None,
    ) -> dict:
        """构造 debugInformation 子对象

        仅包含非空字段。
        """
        info = {}
        if processes:
            info["processes"] = processes
        if gui_windows:
            info["guiWindows"] = gui_windows
        if pending_events:
            info["pendingEvents"] = pending_events
        if hint:
            info["hint"] = hint
        if elapsed_ms is not None:
            info["elapsedMs"] = elapsed_ms
        return info

    @staticmethod
    def list_result(sessions: list, hint: str) -> dict:
        """List 命令响应"""
        return {"commandType": "list", "sessions": sessions, "hint": hint}

    @staticmethod
    def kill_result(code: int, msg: str) -> dict:
        """Kill 命令响应"""
        return {"commandType": "kill", "code": code, "msg": msg}

    @staticmethod
    def stop_result(code: int, msg: str) -> dict:
        """Stop 命令响应"""
        return {"commandType": "stop", "code": code, "msg": msg}

    @staticmethod
    def closewin_result(closed: bool, hwnd: int, message: Optional[str] = None) -> dict:
        """Closewin 命令响应

        Args:
            closed: 是否成功关闭。
            hwnd:   窗口句柄。
            message: 错误描述（条件字段，仅 closed=False 时使用）。
        """
        result = {"commandType": "closewin", "closed": closed, "hwnd": hwnd}
        if message is not None:
            result["message"] = message
        return result

    @staticmethod
    def events_result(session_id: str, events: list, count: int, hint: str) -> dict:
        """Events 命令响应"""
        return {
            "commandType": "events",
            "sessionId": session_id,
            "pendingEvents": events,
            "count": count,
            "hint": hint,
        }

    @staticmethod
    def status_result(
        running: bool,
        pid: int,
        port: int,
        uptime,
        active_sessions: int,
        ended_sessions: int,
        web_url: str,
    ) -> dict:
        """Status 响应"""
        return {
            "type": "status",
            "running": running,
            "pid": pid,
            "port": port,
            "uptime": uptime,
            "activeSessions": active_sessions,
            "endedSessions": ended_sessions,
            "webUrl": web_url,
        }

    # ════════════════════════════════════════════════════════════
    #  WebSocket 响应（使用 type）
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def ws_session_list(sessions: list) -> dict:
        return {"type": "session_list", "sessions": sessions}

    @staticmethod
    def ws_shell_list(shells: dict) -> dict:
        return {"type": "shell_list", "shells": shells}

    @staticmethod
    def ws_system_stats(cpu, memory) -> dict:
        return {"type": "system_stats", "cpu": cpu, "memory": memory}

    @staticmethod
    def ws_history_list(sessions: list) -> dict:
        return {"type": "history_list", "sessions": sessions}

    @staticmethod
    def ws_subscribed(session_id: str, **fields) -> dict:
        return {"type": "subscribed", "sessionId": session_id, **fields}

    @staticmethod
    def ws_output(session_id: str, session_uid: str = "", data: str = "", stream: str = "", encoding: str = "") -> dict:
        msg = {
            "type": "output",
            "sessionId": session_id,
            "data": data,
            "stream": stream,
            "encoding": encoding,
        }
        if session_uid:
            msg["sessionUid"] = session_uid
        return msg

    @staticmethod
    def ws_session_ended(session_id: str, session_uid: str = "", exit_code=None, error_message=None) -> dict:
        msg = {
            "type": "session_ended",
            "sessionId": session_id,
            "exitCode": exit_code,
            "errorMessage": error_message,
        }
        if session_uid:
            msg["sessionUid"] = session_uid
        return msg

    @staticmethod
    def ws_session_event(session_id: str, session_uid: str = "", event: dict = None) -> dict:
        msg = {"type": "session_event", "sessionId": session_id, "event": event or {}}
        if session_uid:
            msg["sessionUid"] = session_uid
        return msg

    @staticmethod
    def ws_clipboard(session_id: str, session_uid: str = "", selection: str = "", data: str = "") -> dict:
        """OSC 52 剪贴板写推送（应用 → 终端 → 前端写系统剪贴板）"""
        msg = {
            "type": "clipboard",
            "sessionId": session_id,
            "selection": selection,
            "data": data,
        }
        if session_uid:
            msg["sessionUid"] = session_uid
        return msg

    @staticmethod
    def ws_unsubscribed() -> dict:
        return {"type": "unsubscribed"}

    @staticmethod
    def ws_history_deleted(session_id: str) -> dict:
        return {"type": "history_deleted", "sessionId": session_id}

    # ════════════════════════════════════════════════════════════
    #  WebSocket 响应 — VNC 远程桌面
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def ws_vnc_status(status: dict) -> dict:
        """VNC 状态响应（包含运行状态/端口/密码/可用性）。

        Args:
            status: VncAdapter.get_status() 返回的字典。
        """
        return {"type": "vnc_status", **status}

    @staticmethod
    def ws_vnc_started(connection_info: dict) -> dict:
        """VNC 启动成功响应（包含前端连接所需信息）。

        Args:
            connection_info: {vnc_port, password, vnc_pid}
        """
        return {"type": "vnc_started", **connection_info}

    @staticmethod
    def ws_vnc_stopped() -> dict:
        """VNC 停止响应。"""
        return {"type": "vnc_stopped"}

    @staticmethod
    def ws_vnc_error(message: str = "", code=None, params=None) -> dict:
        """VNC 错误响应（带 i18n 错误码）。

        Args:
            message: 错误描述（后端语言无关，通常为英文或空）。
            code:    i18n 错误码，前端字典 key。
            params:  插值参数（如 {error: <原始异常>}）。
        """
        resp = {"type": "vnc_error", "message": message}
        if code:
            resp["code"] = code
        if params:
            resp["params"] = params
        return resp

    # ════════════════════════════════════════════════════════════
    #  WebSocket 响应 — FastScreen 屏幕查看
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def ws_fs_status(status: dict) -> dict:
        """Screenshare 状态响应（包含可用性 + 活跃会话数）。

        Args:
            status: ScreenshareAdapter.get_status() 返回的字典。
        """
        return {"type": "fs_status", **status}

    @staticmethod
    def ws_fs_targets(targets: dict) -> dict:
        """Screenshare 目标列表响应（显示器 + 窗口）。

        Args:
            targets: ScreenshareAdapter.list_targets() 返回的字典。
        """
        return {"type": "fs_targets", **targets}

    @staticmethod
    def ws_fs_error(message: str = "", code=None, params=None) -> dict:
        """FastScreen 错误响应（带 i18n 错误码）。

        Args:
            message: 错误描述（后端语言无关，通常为英文或空）。
            code:    i18n 错误码，前端字典 key。
            params:  插值参数（如 {error: <原始异常>}）。
        """
        resp = {"type": "fs_error", "message": message}
        if code:
            resp["code"] = code
        if params:
            resp["params"] = params
        return resp

    # ════════════════════════════════════════════════════════════
    #  WebSocket 响应 — 鼠标增强光标定位器
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def ws_cursor_locator_status(status: dict) -> dict:
        """光标定位器状态响应。

        Args:
            status: CursorLocatorAdapter.get_status() 返回的字典。
        """
        return {"type": "cursor_locator_status", **status}

    @staticmethod
    def ws_cursor_locator_started() -> dict:
        """光标定位器启动成功响应。"""
        return {"type": "cursor_locator_started"}

    @staticmethod
    def ws_cursor_locator_stopped() -> dict:
        """光标定位器停止响应。"""
        return {"type": "cursor_locator_stopped"}

    @staticmethod
    def ws_cursor_locator_error(message: str = "", code=None, params=None) -> dict:
        """光标定位器错误响应（带 i18n 错误码）。

        Args:
            message: 错误描述（后端语言无关，通常为英文或空）。
            code:    i18n 错误码，前端字典 key。
            params:  插值参数（如 {error: <原始异常>}）。
        """
        resp = {"type": "cursor_locator_error", "message": message}
        if code:
            resp["code"] = code
        if params:
            resp["params"] = params
        return resp
