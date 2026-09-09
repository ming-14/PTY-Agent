"""多路唤醒原语 — 等待循环的统一睡眠/唤醒锚

Session 的等待循环同时盯着多个**平级**返回条件（触发正则命中、GUI 新窗口、
进程崩溃、进程退出、静默超时）。这些条件的产生者分布在不同线程：

- reader 线程：正则匹配命中（TriggerMatcher）
- GUI 消息泵线程：WinEvent hook 回调（GuiWindowMonitor → GuiDetector）
- Job IOCP 线程：进程崩溃（ProcessMonitor）
- reader 线程：进程退出（Session._on_reader_exit）
- 请求线程自身：stop / 超时

若各用各的 Event，等待循环只能挑一个来 wait，其余条件就得靠轮询发现，
延迟退化成轮询周期。`WakeSignal` 让所有生产者共用一个唤醒锚，任一方
`notify()` 都能立刻唤醒等待方，使各条件获得同量级的响应延迟。

**协议约定**（违反会丢唤醒）：

- 生产者：**先发布状态，再 `notify()`**。等待方复位后重新判定的是状态，
  因此复位可能吞掉的只是"已反映在状态里"的唤醒。
- 等待方：每轮 `reset()` → 判定全部条件 → `wait(tick)`。
  复位在判定之前，故判定与等待之间到达的 notify 会让 wait 立即返回。
- `wait` 的超时 tick 只作为安全网（防止极端情况下漏醒），不是延迟来源。
"""

import threading


class WakeSignal:
    """唤醒锚：任一生产者 notify 即唤醒等待方（电平语义，由等待方复位）"""

    __slots__ = ("_event",)

    def __init__(self):
        self._event = threading.Event()

    def notify(self) -> None:
        """唤醒等待方（生产者发布状态后调用；置位幂等）"""
        self._event.set()

    def reset(self) -> None:
        """清除唤醒状态（仅等待方在每轮判定前调用）"""
        self._event.clear()

    def wait(self, timeout: float) -> bool:
        """等待一次唤醒

        Args:
            timeout: 最长等待秒数（安全网上限）。

        Returns:
            True 表示被唤醒，False 表示超时。
        """
        return self._event.wait(timeout)

    @property
    def triggered(self) -> bool:
        """当前是否处于已唤醒状态（调试/测试用）"""
        return self._event.is_set()
