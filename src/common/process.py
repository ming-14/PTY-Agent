"""跨侧共享进程工具 —— 进程存在性探测

pid_exists 被 daemon 控制（src/daemonctl）与 daemon 自身（server.py 启动检查）共用。
统一走 psutil.pid_exists（跨平台，权限不足时视为存在，与历史语义一致）。
"""

import psutil


def pid_exists(pid: int) -> bool:
    """检查指定 PID 的进程是否存在（跨平台）

    底层语义：pid<=0 视为不存在（0 表示"未设置"，psutil 会误判为系统空闲进程）；
    进程不存在返回 False；存在或虽被拒权但无法证伪时返回 True。
    """
    if pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False