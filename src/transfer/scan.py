"""传输目录树扫描 —— 全量传输（scp -r 语义，不过滤隐藏/忽略项）

单文件场景返回单条目（relpath=""），目录场景递归整棵树（含空目录）。
条目上限 TRANSFER_MAX_FILES 防御（超出报错，不做分帧重组）。

性能：使用 os.scandir + DirEntry.stat()（Windows 上直接利用目录查询
缓存，避免每个文件一次额外 os.stat 系统调用）。
"""

import os
from typing import List

from ..config.transfer import TRANSFER_MAX_FILES
from .common import ENTRY_DIR, ENTRY_FILE, TransferError, entry


def _scan_dir(dirpath: str, root: str, entries: List[dict], count: int) -> int:
    """递归扫描一个目录（对齐 os.walk 语义：目录条目在前、symlink 目录不递归）

    Returns:
        更新后的条目计数。
    """
    try:
        with os.scandir(dirpath) as it:
            children = list(it)
    except OSError:
        return count  # 权限不足等错误：跳过该目录（对齐 os.walk onerror=None）

    def _is_dir(e) -> bool:
        try:
            return e.is_dir()
        except OSError:
            return False

    # 目录在前、文件在后（各自按名排序），对齐 os.walk 输出顺序
    dir_entries = sorted((e for e in children if _is_dir(e)), key=lambda e: e.name)
    file_entries = sorted(
        (e for e in children if not _is_dir(e)), key=lambda e: e.name
    )

    for e in dir_entries:
        rel = os.path.relpath(e.path, root).replace(os.sep, "/")
        entries.append(entry(rel, ENTRY_DIR))
        count += 1
        if count > TRANSFER_MAX_FILES:
            raise TransferError(
                "too many entries (> %d); split into smaller batches"
                % TRANSFER_MAX_FILES
            )
        # os.walk 默认不递归 symlink 目录（仅作为目录条目列出）
        try:
            is_symlink = e.is_symlink()
        except OSError:
            is_symlink = False
        if not is_symlink:
            count = _scan_dir(e.path, root, entries, count)

    for e in file_entries:
        rel = os.path.relpath(e.path, root).replace(os.sep, "/")
        st = e.stat()
        entries.append(entry(rel, ENTRY_FILE, st.st_size, st.st_mtime))
        count += 1
        if count > TRANSFER_MAX_FILES:
            raise TransferError(
                "too many entries (> %d); split into smaller batches"
                % TRANSFER_MAX_FILES
            )
    return count


def scan_tree(root: str) -> List[dict]:
    """扫描本地/远端路径为清单条目列表（根自身可为文件或目录）

    Args:
        root: 绝对路径（文件或目录）

    Returns:
        entries：目录场景含根自身目录条目 + 子树；文件场景单条目 relpath=""
    """
    if os.path.isfile(root):
        st = os.stat(root)
        return [entry("", ENTRY_FILE, st.st_size, st.st_mtime)]

    if not os.path.isdir(root):
        raise TransferError("path does not exist: %s" % root)

    entries = [entry("", ENTRY_DIR)]
    _scan_dir(root, root, entries, 1)
    return entries
