"""领域实体与值对象。"""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ActiveSession:
    """活跃会话摘要。"""

    id: str
    uid: str
    command: str
    running: bool
    start_time: float


@dataclass
class HistorySession:
    """历史会话摘要。"""

    id: str
    command: str
    pty_type: str
    encoding: str
    start_time: float
    end_time: Optional[float]
    exit_code: Optional[int]
    error_message: Optional[str]
    uid: str = ""


@dataclass
class HistoryDetail:
    """历史会话详情。"""

    id: str
    command: str
    pty_type: str
    cols: int
    rows: int
    encoding: str
    start_time: float
    end_time: Optional[float]
    exit_code: Optional[int]
    error_message: Optional[str]
    uid: str = ""
    replay: str = ""
    snapshot: str = ""
    screen_buffer_z: Optional[str] = None
    screen_buffer_meta: Optional[dict] = None
    output_gz: Optional[str] = None
    output_gz_original_len: Optional[int] = None
    events: Optional[list] = None


@dataclass
class OutputChunk:
    """输出块。"""

    session_id: str
    data: str
    stream: str
    encoding: str


@dataclass
class SystemStats:
    """系统资源统计。"""

    cpu: Optional[float]
    memory: Optional[float]


@dataclass
class SessionEndedInfo:
    """会话结束信息。"""

    session_id: str
    exit_code: Optional[int]
    error_message: Optional[str]


@dataclass
class SessionEvent:
    """会话事件。"""

    session_id: str
    event: dict


@dataclass
class SessionDetail:
    """活跃会话详情。"""

    id: str
    uid: str
    command: str
    pty_type: str
    cols: int
    rows: int
    encoding: str
    start_time: float
    running: bool
    exit_code: Optional[int]
    error_message: Optional[str]
    cwd: str = ""
    process_tree: Optional[Any] = None
    process_details: Optional[dict] = None
    events: Optional[list] = None
    gui_windows: Optional[list] = None
    output_size: int = 0
