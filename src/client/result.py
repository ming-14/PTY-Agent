"""类型化结果模型 — CLI 表示层（Presenter）的输入契约

把 daemon 响应（经信封拆解后的扁平 body）规范化为类型化 Result，
供 presenter 按类型渲染。所有模型暴露统一的：
- ``ok``: 是否成功（决定进程退出码）
- ``kind``: 呈现意图（session/list/keyval/events/error/message/...）
- ``raw``: 原始响应 body（兜底）
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# 会话类命令（exec/send/read/mouse）
_SESSION_CMDS = {"exec", "send", "read", "mouse"}


@dataclass
class Result:
    """结果基类"""
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return True

    @property
    def kind(self) -> str:
        return "result"


@dataclass
class ErrorResult(Result):
    """错误结果 → stderr + 退出码 1"""
    message: str = ""
    code: Optional[str] = None

    @property
    def ok(self) -> bool:
        return False

    @property
    def kind(self) -> str:
        return "error"


@dataclass
class MessageResult(Result):
    """通用信息/警告/配置 → 按通道输出"""
    msg_type: str = "info"  # info | warning | config | help | raw
    text: str = ""

    @property
    def kind(self) -> str:
        return "message"


@dataclass
class SessionResult(Result):
    """exec/send/read/mouse：内容 + 会话状态 + 渲染注解"""
    command_type: str = "exec"
    session_id: str = ""
    uid: str = ""
    output: str = ""
    stderr: str = ""
    output_offset: int = 0
    reason: str = "ok"
    program: dict = field(default_factory=dict)
    hint: str = ""
    terminal_state: Optional[dict] = None
    meta: dict = field(default_factory=dict)  # debug/sessionDefaults 等
    matches: list = field(default_factory=list)  # mouse grep 命中结果（坐标区域）

    @property
    def running(self) -> bool:
        return bool(self.program.get("running"))

    @property
    def kind(self) -> str:
        return "session"


@dataclass
class ListResult(Result):
    sessions: list = field(default_factory=list)
    hint: str = ""

    @property
    def kind(self) -> str:
        return "list"


@dataclass
class StatusResult(Result):
    running: bool = False
    pid: Optional[int] = None
    port: Optional[int] = None
    uptime: Optional[float] = None
    active_sessions: int = 0
    ended_sessions: int = 0
    web_url: str = ""

    @property
    def kind(self) -> str:
        return "keyval"


@dataclass
class EventsResult(Result):
    events: list = field(default_factory=list)
    count: int = 0
    hint: str = ""

    @property
    def kind(self) -> str:
        return "events"


@dataclass
class KillResult(Result):
    code: int = 0
    session_id: str = ""
    msg: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def kind(self) -> str:
        return "single"


@dataclass
class StopResult(Result):
    code: int = 0
    msg: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def kind(self) -> str:
        return "single"


@dataclass
class CloseWinResult(Result):
    closed: bool = False
    hwnd: Optional[int] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.closed)

    @property
    def kind(self) -> str:
        return "single"


@dataclass
class PluginResult(Result):
    session_id: str = ""
    action: str = ""
    plugins: list = field(default_factory=list)
    info: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    message: str = ""
    result: Any = None

    @property
    def ok(self) -> bool:
        return not self.raw.get("type") == "error"

    @property
    def kind(self) -> str:
        return "list"


@dataclass
class FileResult(Result):
    """file_* 命令：内容/匹配项/结果文本 + 摘要

    upload/download 逐文件失败（error/failed 非空）标记为失败，其余 file_* 恒成功。
    """
    command_type: str = "file_read"
    body: str = ""
    summary: str = ""
    error: str = ""
    failed: list = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "file"

    @property
    def ok(self) -> bool:
        # 逐文件传输失败（error 或 failed 非空）→ 非 0 退出；file_read/write/edit/grep/glob
        # 的错误响应走 ErrorResult，不会携带 error/failed，此处不受影响
        return not (self.error or self.failed)


@dataclass
class WaitResult(Result):
    """wait 命令：请求等待秒数 + 实际耗时 + 待消费通知摘要"""
    timeout: float = 0.0
    elapsed: float = 0.0
    notifications: list = field(default_factory=list)  # [{nid, sessionId, triggerReturnReason, createdAt}]

    @property
    def kind(self) -> str:
        return "wait"


@dataclass
class WorkflowResult(Result):
    action: str = ""
    data: Any = None

    @property
    def kind(self) -> str:
        return "workflow"


# 常见错误消息 → 稳定错误码（客户端侧分类，供分支处理；服务端显式 code 优先）
_ERROR_CODE_RULES = (
    ("not found", "NOT_FOUND"),
    ("不存在", "NOT_FOUND"),
    ("already exists", "ALREADY_EXISTS"),
    ("已存在", "ALREADY_EXISTS"),
    ("too long", "INVALID_ARG"),
    ("invalid", "INVALID_ARG"),
    ("Authentication failed", "UNAUTHORIZED"),
    ("认证失败", "UNAUTHORIZED"),
    ("no response", "NO_RESPONSE"),
    ("daemon not responding", "NO_RESPONSE"),
    ("daemon not running", "DAEMON_NOT_RUNNING"),
    ("Interrupted", "INTERRUPTED"),
)


def _classify_error(message: str) -> str:
    if not message:
        return "ERROR"
    for needle, code in _ERROR_CODE_RULES:
        if needle.lower() in message.lower():
            return code
    return "ERROR"


def from_response(resp) -> Result:
    """把 daemon 响应 body 规范化为类型化 Result

    Args:
        resp: 已拆信封的扁平响应 body（None 视为连接失败）。
    """
    if resp is None:
        return ErrorResult(message="daemon not responding", code="NO_RESPONSE")

    if isinstance(resp, Result):
        return resp

    if not isinstance(resp, dict):
        return MessageResult(msg_type="raw", text=str(resp))

    rtype = resp.get("type")
    if rtype == "error":
        message = resp.get("message", "")
        return ErrorResult(message=message, code=resp.get("code") or _classify_error(message))
    if rtype in ("info", "warning"):
        return MessageResult(msg_type=rtype, text=resp.get("message", ""))
    if rtype == "config":
        return MessageResult(msg_type="config", text=resp.get("content", ""))
    if rtype == "pong":
        return MessageResult(msg_type="info", text="pong")
    if rtype == "status":
        return StatusResult(
            running=bool(resp.get("running")),
            pid=resp.get("pid"),
            port=resp.get("port"),
            uptime=resp.get("uptime"),
            active_sessions=resp.get("activeSessions", 0),
            ended_sessions=resp.get("endedSessions", 0),
            web_url=resp.get("webUrl", ""),
            raw=resp,
        )
    if rtype == "wait":
        return WaitResult(
            timeout=resp.get("timeout", 0.0),
            elapsed=resp.get("elapsed", 0.0),
            notifications=resp.get("notifications", []),
            raw=resp,
        )
    if rtype == "svg":
        # 带会话上下文的 svg 响应（exec/send/read/mouse --response-format svg）：
        # 走 SessionResult 渲染（svg 标签框 + 状态行）；否则裸 svg 文本
        if resp.get("commandType"):
            return SessionResult(
                command_type=resp.get("commandType", ""),
                session_id=resp.get("sessionId", ""),
                uid=resp.get("uid", ""),
                output=resp.get("data", "") or resp.get("outputStream", ""),
                stderr=resp.get("stderrOutput", ""),
                reason=resp.get("triggerReturnReason", ""),
                program=resp.get("program", {}),
                hint=resp.get("hint", ""),
                terminal_state=resp.get("terminalState"),
                meta=_collect_meta(resp),
                matches=[],
                raw=resp,
            )
        return MessageResult(msg_type="svg", text=resp.get("data", ""), raw=resp)

    ct = resp.get("commandType", "")
    if ct in _SESSION_CMDS:
        return SessionResult(
            command_type=ct,
            session_id=resp.get("sessionId", ""),
            uid=resp.get("uid", ""),
            output=resp.get("outputStream", ""),
            stderr=resp.get("stderrOutput", ""),
            output_offset=resp.get("outputOffset", 0),
            reason=resp.get("triggerReturnReason", ""),
            program=resp.get("program", {}),
            hint=resp.get("hint", ""),
            terminal_state=resp.get("terminalState"),
            meta=_collect_meta(resp),
            matches=resp.get("matches", []) or [],
            raw=resp,
        )
    if ct == "list":
        return ListResult(sessions=resp.get("sessions", []), hint=resp.get("hint", ""), raw=resp)
    if ct == "events":
        return EventsResult(
            events=resp.get("pendingEvents", []),
            count=resp.get("count", 0),
            hint=resp.get("hint", ""),
            raw=resp,
        )
    if ct == "kill":
        return KillResult(code=resp.get("code", 0), session_id=resp.get("sessionId", ""), msg=resp.get("msg", ""), raw=resp)
    if ct == "stop":
        return StopResult(code=resp.get("code", 0), msg=resp.get("msg", ""), raw=resp)
    if ct == "closewin":
        return CloseWinResult(closed=bool(resp.get("closed")), hwnd=resp.get("hwnd"), message=resp.get("message", ""), raw=resp)
    if ct == "plugin":
        return PluginResult(
            session_id=resp.get("sessionId", ""),
            action=resp.get("action", ""),
            plugins=resp.get("plugins", []),
            info=resp.get("info") or {},
            config=resp.get("config") or {},
            message=resp.get("message", ""),
            result=resp.get("result"),
            raw=resp,
        )
    if ct and ct.startswith("file_"):
        body = resp.get("content") or resp.get("stdout") or ""
        if not body:
            if ct == "file_grep":
                body = _format_grep_matches(resp.get("matches", []))
            elif ct == "file_glob":
                body = "\n".join(resp.get("files", []))
        return FileResult(
            command_type=ct,
            body=body,
            summary=_file_summary(ct, resp),
            error=resp.get("error") or "",
            failed=list(resp.get("failed") or []),
            raw=resp,
        )
    if ct == "workflow":
        return WorkflowResult(action=resp.get("action", ""), data=resp.get("result", resp), raw=resp)
    # 兜底：未知/其他 → 展示原始 JSON 的关键信息，不原样 dump
    return MessageResult(msg_type="raw", text=_fallback_text(resp), raw=resp)


def _collect_meta(resp: dict) -> dict:
    # debugInformation 嵌套在 resp["program"] 中，由 presenter 直接从 program 取；
    # 此处只收集顶层元信息
    meta = {}
    for key in ("sessionDefaults", "format"):
        if resp.get(key) is not None:
            meta[key] = resp[key]
    return meta


def _file_summary(ct: str, resp: dict) -> str:
    if ct in ("file_read", "file_write", "file_edit"):
        return resp.get("result", "") or ""
    if ct in ("file_grep", "file_glob"):
        n = len(resp.get("matches", []) or resp.get("files", []))
        return f"{n} match(es)" if n else ""
    if ct in ("file_upload", "file_download"):
        return _transfer_summary(ct, resp)
    return resp.get("result", "") or resp.get("message", "") or ""


def _transfer_summary(ct: str, resp: dict) -> str:
    """upload/download 汇总摘要：失败含错误详情与未传输计数，成功含传输/跳过统计"""
    label = "上传" if ct == "file_upload" else "下载"
    failed = resp.get("failed") or []
    error = resp.get("error") or ""
    if error:
        text = f"{label}失败: {error}"
        if failed:
            text += f"（{len(failed)} 个文件未传输）"
        return text
    if failed:
        names = "、".join(str(x) for x in failed)
        return f"{label}失败: {len(failed)} 个文件未传输: {names}"
    transferred = len(resp.get("transferred") or [])
    skipped = len(resp.get("skipped") or [])
    parts = [f"{transferred} 个文件已传输"]
    if skipped:
        parts.append(f"{skipped} 个跳过")
    return f"{label}完成: {', '.join(parts)}"


def _format_grep_matches(matches: list) -> str:
    """file_grep 匹配列表 → 文本行（path:lineNumber: content）"""
    return "\n".join(
        "%s:%s: %s" % (m.get("path", ""), m.get("lineNumber", ""), m.get("content", ""))
        for m in matches
    )


def _fallback_text(resp: dict) -> str:
    text = resp.get("message") or resp.get("hint") or ""
    if text:
        return text
    parts = []
    if "commandType" in resp:
        parts.append(resp["commandType"])
    if resp.get("outputStream"):
        parts.append(resp["outputStream"])
    return "; ".join(parts) if parts else ""