"""返回条件与返回原因的统一声明 — 执行链路的单一事实来源

把"何时返回/返回什么/为什么返回"从各 handler 与 execution 流程里零散读取的
``msg.get(...)`` 与字符串字面量，收敛到本模块：

- ``ReturnConditions``：一次从请求消息解释全部返回条件（trigger/newline/fresh/
  idle/snapshot/keep_ansi/full/explicit_timeout），exec/send/read 与 workflow 共用。
- 返回原因词汇（``Reason``）属协议层，见 ``protocol.reasons``。

注意：本模块只做"声明与解释"，不改变等待/监控的时序逻辑，保证行为零变化。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReturnConditions:
    """从请求消息统一解释到的返回条件（执行链单一事实来源）"""

    trigger: Optional[str] = None
    newline: bool = False
    fresh: bool = False
    timeout: float = 120.0
    explicit_timeout: bool = False
    idle_timeout: Optional[float] = None
    idle_after_first: bool = False
    full: bool = False
    keep_ansi: bool = False
    snapshot_diff: bool = False

    @classmethod
    def from_msg(cls, msg: dict) -> "ReturnConditions":
        """一次读取请求消息中的全部返回条件"""
        return cls(
            trigger=msg.get("trigger"),
            newline=msg.get("newline", False),
            fresh=msg.get("fresh", False),
            timeout=msg.get("timeout", 120.0),
            explicit_timeout=msg.get("explicit_timeout", False),
            idle_timeout=msg.get("idle_timeout"),
            idle_after_first=msg.get("idle_after_first_output", False),
            full=msg.get("full", False),
            keep_ansi=msg.get("keep_ansi", False),
            snapshot_diff=msg.get("snapshot_diff", False),
        )

    @property
    def has_trigger(self) -> bool:
        return self.trigger is not None

    @property
    def has_idle(self) -> bool:
        return self.idle_timeout is not None

    @property
    def has_wait(self) -> bool:
        """是否需要等待：有 trigger / 有 idle / 显式 timeout 预等待"""
        return self.has_trigger or self.has_idle or self.explicit_timeout


@dataclass
class RequestContext:
    """请求契约 VO：从请求消息一次解析公共字段（执行链单一事实来源）

    覆盖 exec/send/read/mouse 共用的请求字段（id/command/input/encoding/cwd/env/
    mode/cols/rows/plugins 等）与读路径字段（lines/grep/column/offset/action），
    返回条件内嵌 ReturnConditions（trigger/idle/full/keep_ansi/snapshot_diff/...）。
    仅做"一处解析"的声明层，不参与时序与判定逻辑，保证行为零变化。
    """

    cond: ReturnConditions
    id: str = ""
    command: Optional[str] = None
    input: Optional[str] = None
    pause_offsets: Optional[list] = None
    encoding: Optional[str] = None
    cwd: Optional[str] = None
    env: Optional[dict] = None
    mode: str = "pty"
    cols: Optional[int] = None
    rows: Optional[int] = None
    size: Optional[str] = None
    plugins: list = field(default_factory=list)
    cli_plugins: list = field(default_factory=list)
    json_escaping: bool = False
    send_eol: Optional[str] = None
    lines: Optional[object] = None
    grep: Optional[str] = None
    column: Optional[int] = None
    offset: Optional[int] = None
    action: Optional[str] = None
    t_start: Optional[float] = None

    @classmethod
    def from_msg(cls, msg: dict) -> "RequestContext":
        """一次解析请求消息中的公共字段与返回条件（默认值集中于此）"""
        return cls(
            cond=ReturnConditions.from_msg(msg),
            id=msg.get("id", ""),
            command=msg.get("command"),
            input=msg.get("input", ""),
            pause_offsets=msg.get("pause_offsets"),
            encoding=msg.get("encoding"),
            cwd=msg.get("cwd"),
            env=msg.get("env"),
            mode=msg.get("mode", "pty"),
            cols=msg.get("cols"),
            rows=msg.get("rows"),
            size=msg.get("size"),
            plugins=msg.get("plugins") or [],
            cli_plugins=msg.get("cliPlugins") or [],
            json_escaping=msg.get("json_escaping", False),
            send_eol=msg.get("send_eol"),
            lines=msg.get("lines"),
            grep=msg.get("grep"),
            column=msg.get("column"),
            offset=msg.get("offset"),
            action=msg.get("action"),
            t_start=msg.get("_t_start"),
        )