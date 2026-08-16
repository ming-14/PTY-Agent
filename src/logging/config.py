"""日志配置结构 — 从 config/ 常量组装日志配置

封装从 src/config.daemon / src/config.shared / src/config.client 的模块级常量
到结构化配置对象的转换，使 setup.py 更清晰。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class LoggingConfig:
    """日志装配配置

    Attributes:
        log_dir: 日志文件目录
        log_format: 日志格式串
        log_date_format: 日期格式串
        groups: 分组名 → logger 名列表
        levels: 分组名 → 级别（None 表示不落盘）
        archive_interval: 归档检查间隔（秒）
        queue_size: 异步队列容量
    """

    log_dir: str
    log_format: str
    log_date_format: str
    groups: Dict[str, List[str]] = field(default_factory=dict)
    levels: Dict[str, Optional[int]] = field(default_factory=dict)
    archive_interval: float = 600.0
    queue_size: int = 8192
