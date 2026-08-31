"""线协议信封 + 分组载荷封装（协议域）

把跨切面元数据（版本/方向/类型/消息id/时间戳/呈现意图）与业务载荷分离，
载荷按命令意图分组：请求 op/condition/output/io，为表示层提供稳定契约。

设计目标：
- 信封只在两端组装/拆解（客户端出站套请求信封、daemon dispatcher 拆请求信封），
  内部业务层以扁平 body 交互，接入成本低。
- 四大终端命令（exec/send/read/mouse）载荷分组最细；
  其余命令单一 op 承载，纵向扩展同一信封。
"""

import itertools
import time
from datetime import datetime, timezone

# 协议版本（破坏性变更时递增并做版本协商）
PROTO = 1

_DIR_REQUEST = "request"
_DIR_RESPONSE = "response"

# 载荷分组占用字段（其余字段归入 op）
_CONDITION_FIELDS = {
    "trigger",
    "newline",
    "fresh",
    "timeout",
    "explicit_timeout",
    "idle_timeout",
    "idle_after_first_output",
}
_OUTPUT_FIELDS = {
    "full",
    "keep_ansi",
    "lines",
    "grep",
    "offset",
    "column",
    "snapshot_diff",
    "include_screen_buffer",
    "render_format",
}
_IO_FIELDS = {"encoding", "send_eol", "sendEol"}

# 四大终端命令做最细分组
_TERMINAL_CMDS = {"exec", "send", "read", "mouse"}

# 会话响应载荷分组：data=返回内容，state=状态与原因，meta=渲染注解
# 分组内保留原始字段名，反转时无损还原，客户端业务层可不改动
_RESP_SESSION_CMDS = {"exec", "send", "read", "mouse"}
_RESP_GROUPS = {"data", "state", "meta"}
_RESP_GROUP_MEMBERS = {
    "data": {"outputStream", "stderrOutput", "screenBufferZ", "screenBufferMeta", "snapshotDiagnostics", "svgContent", "imageZ", "imageType"},
    "state": {"sessionId", "uid", "outputOffset", "triggerReturnReason", "program", "stderrOutputOffset"},
    "meta": {"hint", "terminalState", "sessionDefaults", "debugInformation"},
}
# 原样帧流握手：file 传输的握手/ACK 响应不套响应信封，避免破坏传输协议
_RAW_RESP_TYPES = {"file_upload_start", "file_download_start"}

# 类型 → 呈现意图（表示层据此选择渲染通道）
_KIND = {
    "exec": "session",
    "send": "session",
    "read": "session",
    "mouse": "session",
    "list": "list",
    "events": "events",
    "status": "keyval",
}

_mid_counter = itertools.count(1)


def _now_ts() -> str:
    """本地时区精确到毫秒的时间戳（ISO 8601 风格），作防重放窗口时间源"""
    dt = datetime.now(timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _next_mid() -> str:
    """消息 id：毫秒时间 + 进程内自增计数，保证同进程内唯一，用于请求/响应关联"""
    return f"{int(time.time() * 1000)}{next(_mid_counter)}"


def _kind_of(type_: str) -> str:
    return _KIND.get(type_, "cmd")


def _group_terminal(flat: dict) -> dict:
    """把终端命令的扁平请求字段按 操作/返回条件/返回过滤/IO 分组"""
    op: dict = {}
    condition: dict = {}
    output: dict = {}
    io: dict = {}
    for key, value in flat.items():
        if key in _CONDITION_FIELDS:
            condition[key] = value
        elif key in _OUTPUT_FIELDS:
            output[key] = value
        elif key in _IO_FIELDS:
            io[key] = value
        else:
            op[key] = value
    grouped = {"op": op}
    if condition:
        grouped["condition"] = condition
    if output:
        grouped["output"] = output
    if io:
        grouped["io"] = io
    return grouped


def group_request(type_: str, flat: dict) -> dict:
    """把命令请求扁平 dict 分组为 payload

    Args:
        type_: 命令类型。
        flat:  命令请求的扁平 dict（type 字段入 op，不做特殊处理）。

    Returns:
        分组后的 payload：终端命令为 {op,condition?,output?,io?}，其余为 {op:{...}}。
    """
    body = dict(flat)
    if type_ not in _TERMINAL_CMDS:
        return {"op": body}
    return _group_terminal(body)


def flatten(payload) -> dict:
    """把分组 payload 还原为扁平 body（内部业务层消费）

    若 payload 非 dict 或不含分组键，原样返回。
    """
    if not isinstance(payload, dict):
        return {} if payload is None else payload
    if "op" not in payload:
        return dict(payload)
    body = dict(payload.get("op") or {})
    for key in ("condition", "output", "io"):
        sub = payload.get(key)
        if isinstance(sub, dict):
            body.update(sub)
    return body


def request(type_: str, flat: dict, kind: str = None) -> dict:
    """构造请求信封（内部业务扁平 dict → 分组 payload + 信封元数据）

    ``auth`` 段承载认证凭证（token/password/pubkey_fp，由提供者填充），
    与业务 payload、签名解耦，供认证器在信封层面校验。
    """
    return {
        "proto": PROTO,
        "dir": _DIR_REQUEST,
        "type": type_,
        "mid": _next_mid(),
        "ts": _now_ts(),
        "kind": kind or _kind_of(type_),
        "auth": {},
        "payload": group_request(type_, flat),
    }


def response(type_: str, body: dict, *, mid=None, kind: str = None) -> dict:
    """构造响应信封（body 为承载数据，后续分组为 data/state/meta）"""
    return {
        "proto": PROTO,
        "dir": _DIR_RESPONSE,
        "type": type_,
        "mid": mid,
        "ts": _now_ts(),
        "kind": kind or "result",
        "payload": body,
    }


def split_response(body: dict) -> dict:
    """把会话响应扁平 body 分组成 data/state/meta（分组内保留原始字段名）"""
    grouped = {"data": {}, "state": {}, "meta": {}}
    for key, value in body.items():
        for group, members in _RESP_GROUP_MEMBERS.items():
            if key in members:
                grouped[group][key] = value
                break
        else:
            grouped["meta"][key] = value
    return grouped


def unsplit_response(payload, type_: str = None) -> dict:
    """把分组的会话响应载荷还原为扁平 body（幂等：非分组载荷原样返回）"""
    if isinstance(payload, dict) and any(k in payload for k in _RESP_GROUPS):
        body: dict = {}
        for group in _RESP_GROUPS:
            sub = payload.get(group)
            if isinstance(sub, dict):
                body.update(sub)
    else:
        body = dict(payload) if isinstance(payload, dict) else {}
    if type_ and "commandType" not in body:
        body["commandType"] = type_
    return body


def wrap_response(body) -> dict:
    """daemon 出站响应包装（线程局部接入 Message.send）

    - 会话响应：分组（data/state/meta）后套响应信封；
    - 非会话响应：载荷原样套信封；
    - file 传输握手（原样帧流）与 pong 保持裸体。

    Returns:
        相同的信封 dict；不适用时原样返回 body。
    """
    if not isinstance(body, dict):
        return body
    command_type = body.get("commandType")
    if command_type in _RAW_RESP_TYPES:
        return body
    type_ = command_type or body.get("type") or "response"
    if type_ == "pong":
        return body
    if command_type in _RESP_SESSION_CMDS:
        payload = split_response(body)
    else:
        payload = dict(body)
    return {
        "proto": PROTO,
        "dir": _DIR_RESPONSE,
        "type": type_,
        "mid": None,
        "ts": _now_ts(),
        "kind": _kind_of(type_),
        "payload": payload,
    }


def unwrap(msg):
    """拆信封，返回 (type_, body, envelope)

    - 信封消息：请求按分组还原扁平 body；响应按 data/state/meta 还原扁平 body。
    - 非信封消息（如裸 ping）：原样返回 (msg['type'], msg, None)。

    拆出的 body 会补入 ``type`` 字段，方便内部业务层按原语义读取。
    """
    if not isinstance(msg, dict):
        return "", {}, msg
    direction = msg.get("dir")
    if direction in (_DIR_REQUEST, _DIR_RESPONSE):
        type_ = msg.get("type", "")
        payload = msg.get("payload")
        if direction == _DIR_REQUEST:
            body = flatten(payload)
        else:
            body = unsplit_response(payload, type_)
        if type_:
            body.setdefault("type", type_)
        return type_, body, msg
    return msg.get("type", ""), msg, None