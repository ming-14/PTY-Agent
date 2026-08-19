"""系统资源统计提供者实现。

统一以 psutil 提供 CPU/内存使用率（psutil 为核心必装依赖，见 requirements.txt），
不再保留 ctypes//proc 降级兜底。
"""

import psutil

from ...application.ports import SystemStatsProvider
from ...domain.entities import SystemStats


class SystemStatsProviderImpl(SystemStatsProvider):
    """系统资源统计提供者实现。"""

    async def get_stats(self) -> SystemStats:
        cpu = mem = None
        try:
            cpu = psutil.cpu_percent(interval=0)
        except Exception:
            pass
        try:
            mem = psutil.virtual_memory().percent
        except Exception:
            pass
        return SystemStats(cpu=cpu, memory=mem)