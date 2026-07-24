"""线程执行器实现。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from ..application.ports import ThreadExecutor


class ThreadExecutorImpl(ThreadExecutor):
    """在线程池中执行同步调用，避免阻塞 asyncio 事件循环。"""

    def __init__(self, max_workers: Optional[int] = None):
        self._executor: Optional[ThreadPoolExecutor] = None
        self._max_workers = max_workers
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        # loop 可能被 server 重启后替换，始终获取当前活跃的 loop
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.get_event_loop()
        return self._loop

    def _get_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        return self._executor

    async def run(self, fn: Callable, *args, **kwargs) -> Any:
        loop = self._get_loop()
        executor = self._get_executor()
        if kwargs:
            return await loop.run_in_executor(executor, lambda: fn(*args, **kwargs))
        return await loop.run_in_executor(executor, fn, *args)

    def shutdown(self, wait: bool = True) -> None:
        if self._executor:
            self._executor.shutdown(wait=wait)
            self._executor = None
