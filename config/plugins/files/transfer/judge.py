"""相同文件判定与传输计划 —— 纯函数（upload/download 共用）

判定算法（大小第一，mtime 映射第二）：

    classify(远端不存在)                      → transfer
    classify(force=True)                      → transfer（强制覆盖；相同仍跳过）
    classify(大小不同)                        → denied（需 --force）
    classify(映射命中：cli_size/cli_mtime 与记录一致 且 远端 mtime 与记录一致) → skip
    classify(其余)                            → denied

语义要点：
- force 不改变"相同"判定（相同文件永不重传），只放行"不同"的覆盖
- 远端被外部修改（mtime 偏离记录）→ 视为不同 → 默认拒绝
- CLI 文件内容变但 mtime/size 未变 → 误判相同（用户指定方案的固有局限）
"""

from typing import List, Optional

from src.client.transfer.common import ENTRY_DIR, ENTRY_FILE

# classify 结果
TRANSFER = "transfer"
SKIP = "skip"
DENIED = "denied"


def classify(
    remote_exists: bool,
    remote_size: int,
    remote_mtime: float,
    record_size: Optional[int],
    record_cli_mtime: Optional[float],
    record_remote_mtime: Optional[float],
    cli_size: int,
    cli_mtime: float,
    force: bool,
    cli_exists: bool = True,
) -> str:
    """判定单个文件的传输动作

    Args:
        remote_exists: 远端文件是否存在
        remote_size / remote_mtime: 远端当前 stat
        record_*: 映射表记录（无记录传 None）
        cli_size / cli_mtime: CLI 侧文件
        force: 允许覆盖不同文件
        cli_exists: CLI 侧文件是否存在（下载方向本地缺失 → 必须传输，
            本地无文件可保护，不受覆盖判定约束）

    Returns:
        TRANSFER / SKIP / DENIED
    """
    if not cli_exists:
        return TRANSFER
    if not remote_exists:
        return TRANSFER
    if remote_size != cli_size:
        return TRANSFER if force else DENIED
    if (
        record_size is not None
        and record_cli_mtime is not None
        and record_remote_mtime is not None
        and record_size == cli_size
        and record_cli_mtime == cli_mtime
        and record_remote_mtime == remote_mtime
    ):
        return SKIP
    return TRANSFER if force else DENIED


def build_plan(
    entries: List[dict],
    remote_resolver,
    map_getter,
    force: bool,
) -> dict:
    """对清单逐条判定，生成传输计划

    Args:
        entries: 清单条目 [{relpath, kind, size, mtime, exists?}]
            exists 仅下载方向的本地清单提供（False=本地缺失，必须传输）；
            上传方向由 scan_tree 生成，无该键视为 True
        remote_resolver(relpath) -> (exists, size, mtime)：远端文件 stat 解析
            （上传：daemon 侧远端路径；下载：CLI 侧本地文件）
        map_getter(relpath) -> Optional[TransferRecord]：映射记录
        force: 允许覆盖

    Returns:
        {"transfers": [relpath...], "skips": [{"relpath", "reason"}...],
         "denied": [{"relpath"}...], "mkdirs": [relpath...]}
    """
    transfers: List[str] = []
    skips: List[dict] = []
    denied: List[dict] = []
    mkdirs: List[str] = []

    # 目录条目先行收集（mkdir 必须先于其下文件）
    dirs = [e for e in entries if e.get("kind") == ENTRY_DIR]
    files = [e for e in entries if e.get("kind") == ENTRY_FILE]
    for d in dirs:
        mkdirs.append(d["relpath"])

    for f in files:
        relpath = f["relpath"]
        exists, size, mtime = remote_resolver(relpath)
        rec = map_getter(relpath)
        action = classify(
            remote_exists=exists,
            remote_size=size,
            remote_mtime=mtime,
            record_size=rec.cli_size if rec else None,
            record_cli_mtime=rec.cli_mtime if rec else None,
            record_remote_mtime=rec.remote_mtime if rec else None,
            cli_size=f.get("size", 0),
            cli_mtime=f.get("mtime", 0.0),
            force=force,
            cli_exists=f.get("exists", True),
        )
        if action == TRANSFER:
            transfers.append(relpath)
        elif action == SKIP:
            skips.append({"relpath": relpath, "reason": "same file"})
        else:
            denied.append({"relpath": relpath})

    return {
        "transfers": transfers,
        "skips": skips,
        "denied": denied,
        "mkdirs": mkdirs,
    }