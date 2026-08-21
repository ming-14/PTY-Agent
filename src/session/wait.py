"""统一"为何返回"等待引擎骨架 — 三个等待循环的单一迭代来源

P0-B 完整统一：把 exec 等待循环 / 子进程 wait_for_trigger / 无 trigger 复查的
公共迭代结构（cancel 检查、remaining/timeout 判定、循环）收敛到 ``wait_reason``
一处。各循环的**检查顺序**与**等待原语**经 ``iteration`` 回调完整保留，保证行为
零变化；后台监控线程的"低延迟主动判定 vs 2s 兜底"冗余按设计保留，不硬去重
（硬去重会改变崩溃/窗口检测时序，属文档红线）。
"""

import time
from typing import Optional

from ..protocol.reasons import Reason


class _NoReturn:
    """等待自然结束哨兵：调用方沿用初始 reason（如无 trigger 分支的静默超时）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "NO_RETURN"


NO_RETURN = _NoReturn()


def wait_reason(
    *,
    deadline: float,
    cancel_event: Optional[object],
    iteration,
    on_timeout=None,
    on_cancel=None,
):
    """迭代等待直到条件命中 / 超时 / 取消（统一"为何返回"引擎）

    Args:
        deadline: 绝对截止时刻（time.time() 语义）
        cancel_event: threading.Event；置位时以 (False, Reason.CANCELLED) 提前返回
        iteration: (remaining: float) -> Optional[(matched, reason)]；一轮完整迭代
            （含条件检查与等待原语），返回非 None 即作为返回原因，None 继续下一轮
        on_timeout: () -> (matched, reason)；到期返回原因。None 默认
            (False, Reason.TIMEOUT)；返回 NO_RETURN 表示自然结束（沿用初始 reason）
        on_cancel: () -> None；取消时先调用（供调用方打日志），可省略

    Returns:
        (matched, reason) 或 NO_RETURN
    """
    while True:
        if cancel_event is not None and cancel_event.is_set():
            if on_cancel is not None:
                on_cancel()
            return False, Reason.CANCELLED
        remaining = deadline - time.time()
        if remaining <= 0:
            if on_timeout is None:
                return False, Reason.TIMEOUT
            return on_timeout()
        result = iteration(remaining)
        if result is not None:
            return result
