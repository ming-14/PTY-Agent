"""返回原因统一词汇（协议域，最内层）

"为何返回" 的受控词汇单一来源：等待循环产出 Reason 成员，
对外 triggerReturnReason 经 OUTWARD_REASON 映射后仍是 Reason 成员
（str 子类枚举，与字符串比较/序列化完全等价，行为零变化）。
"""

from enum import Enum


class Reason(str, Enum):
    """返回原因（原始原因 + 对外原因两层词汇）

    原始原因由等待循环/会话状态产出（matched/timeout/idle_timeout/...）；
    对外原因（trigger_matched/program_crashed/...）由 map_reason 权威判定后
    写入响应的 triggerReturnReason。成员为 str 子类，与字符串字面量
    比较（==）与 JSON 序列化等价，替换散落字符串无需改动调用方。
    """

    OK = "ok"
    MATCHED = "matched"
    TIMEOUT = "timeout"
    IDLE_TIMEOUT = "idle_timeout"
    ENDED = "ended"
    CRASHED = "crashed"
    GUI_DETECTED = "gui_detected"
    CANCELLED = "cancelled"

    # 对外原因（经 map_reason 权威判定后的对外值）
    TRIGGER_MATCHED = "trigger_matched"
    TRIGGER_TIMEOUT = "trigger_timeout"
    PROGRAM_ENDED = "program_ended"
    PROGRAM_CRASHED = "program_crashed"


# 原始原因 → 对外原因映射（map_reason 的权威来源）
OUTWARD_REASON = {
    Reason.OK: Reason.OK,
    Reason.MATCHED: Reason.TRIGGER_MATCHED,
    Reason.TIMEOUT: Reason.TRIGGER_TIMEOUT,
    Reason.IDLE_TIMEOUT: Reason.IDLE_TIMEOUT,
    Reason.ENDED: Reason.PROGRAM_ENDED,
    Reason.CRASHED: Reason.PROGRAM_CRASHED,
    Reason.GUI_DETECTED: Reason.GUI_DETECTED,
    Reason.CANCELLED: Reason.CANCELLED,
}