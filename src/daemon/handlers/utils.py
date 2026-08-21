import re
from typing import Optional

from ...config.common import parse_terminal_size
from ...config.encoding import is_valid_encoding
from ...protocol.message import Message
from ...protocol.response import Response
from ...session.manager import SessionManager
from ...logging import get_logger

_logger = get_logger("pty-daemon")


def validate_field(value, name: str, max_len: int, conn) -> bool:
    if isinstance(value, str) and len(value) > max_len:
        Message.send(
            conn, Response.error(f"Parameter '{name}' too long (max {max_len} chars)")
        )
        return False
    return True


def apply_client_defaults(session, msg: dict, conn=None) -> bool:
    """把 client_defaults 合入 session.client_config（daemon 侧权威落点）

    对 encoding 键做与 CLI 侧同一白名单校验：非法编码拒绝写入并返回
    type:error（conn 可用时直接发送；conn 缺失时抛出 ValueError 兜底，绝不平静
    采纳非法值）。其余键（timeout/newline/keep-ansi/debug/...）行为不变。

    Returns:
        True 表示已应用（或无可应用内容）；False 表示校验失败，调用方应中止。
    """
    client_defaults = msg.get("client_defaults")
    if not client_defaults or not isinstance(client_defaults, dict):
        return True
    bad_encoding = client_defaults.get("encoding")
    if bad_encoding is not None and not is_valid_encoding(bad_encoding):
        err = (
            f"Invalid encoding: {bad_encoding!r}. "
            "Use a valid codec name (e.g. utf-8, gbk, cp936, latin-1) "
            "or leave it unset for auto detection."
        )
        if conn is not None:
            Message.send(conn, Response.error(err))
            return False
        raise ValueError(err)
    session.client_config.update(client_defaults)
    _apply_resize_default(session, client_defaults)
    return True


def _parse_terminal_size(size_str) -> Optional[tuple]:
    """解析终端尺寸字符串 WxH → (cols, rows)；非法输入返回 None

    委托共享实现 config.common.parse_terminal_size（默认仅要求正整数，
    上界不限），非法/越界以返回 None 表达，保持 daemon 侧宽松语义。
    """
    try:
        return parse_terminal_size(size_str)
    except ValueError:
        return None


def _apply_resize_default(session, client_defaults: dict) -> None:
    """--default terminal-size 对运行中的会话即刻生效（resize）

    client_defaults 携带 terminal_size 时（exec/send/read/mouse 均会下发），
    尺寸与当前不同则调用 session.resize()；子进程模式无终端、会话未运行、
    尺寸未变或解析失败时静默跳过。
    """
    ts = client_defaults.get("terminal_size")
    if not ts:
        return
    if not getattr(session, "running", False):
        return
    if getattr(session, "mode", "pty") == "subprocess":
        return
    size = _parse_terminal_size(ts)
    if size is None:
        _logger.debug("忽略非法 terminal_size=%r（会话 %s）", ts, session.id)
        return
    cols, rows = size
    try:
        if session.cols == cols and session.rows == rows:
            return
    except Exception:
        return
    try:
        session.resize(cols, rows)
        _logger.info(
            "会话 '%s' 经 --default terminal-size 调整尺寸: %dx%d",
            session.id,
            cols,
            rows,
        )
    except Exception:
        _logger.debug("会话 '%s' resize 失败（忽略）: %r", session.id, ts)


def check_ended_session(manager: SessionManager, session_id: str) -> Optional[str]:
    hs = manager._history_store
    if not hs:
        return None
    tag = hs.get_session_tag(session_id)
    return tag if tag == "ended" else None


def validate_request(conn, msg: dict, fields: list) -> bool:
    for name, max_len in fields:
        if not validate_field(msg.get(name), name, max_len, conn):
            return False
    return True


def validate_trigger_regex(trigger, conn) -> bool:
    """校验触发正则在提交前可编译；非法正则拒绝并返回 False

    仅拦截 re.compile 抛出的语法错误（用户输入错误），
    合法但存在 ReDoS 风险的正则由 TriggerMatcher 降级为子串匹配，不在此拒绝。
    """
    if trigger is None:
        return True
    try:
        re.compile(trigger)
    except re.error as e:
        Message.send(
            conn, Response.error(f"Invalid trigger regex: {trigger!r} ({e})")
        )
        return False
    return True


def get_detail(msg: dict) -> str:
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


def prepare_input(
    mode: str,
    input_text: str,
    json_escaping: bool = False,
    send_eol: Optional[str] = None,
) -> tuple:
    """按会话模式统一准备输入文本（转义展开的 daemon 侧权威落点）
    单一事实来源在守护进程：CLI/workflow 只传原始 input + 转义开关 + 显式 eol，
    不再本地展开；本函数依据会话模式决定 {enter}/默认行尾符：
    - 终端(pty) 模式：{enter}→\\r，默认行尾→\\r（模拟终端 Enter）
    - 子进程(subprocess) 模式：{enter}→\\n，默认行尾→\\n

    Args:
        mode:           会话模式 "pty" | "subprocess"。
        input_text:     原始输入文本（含未展开的 {enter} 等转义字面量）。
        json_escaping:  是否启用 JSON + 控制字符转义解码（advsend/workflow json）。
        send_eol:       显式行尾符名称（"cr"/"lf"/"crlf"/"none"）或字面字符；
                        None 时按模式默认（pty=\\r，subprocess=\\n）。

    Returns:
        (展开后文本, 停顿偏移列表)，供 session.write_input(pause_offsets=...) 使用。
    """
    from ...input.text import SEND_EOL_MAP, process_input

    is_sub = mode == "subprocess"
    enter_eol = "\n" if is_sub else "\r"
    if send_eol:
        send_eol_char = SEND_EOL_MAP.get(send_eol, send_eol)
    else:
        send_eol_char = "\n" if is_sub else "\r"
    return process_input(
        input_text,
        json_escaping=json_escaping,
        send_eol=send_eol_char,
        enter_eol=enter_eol,
    )