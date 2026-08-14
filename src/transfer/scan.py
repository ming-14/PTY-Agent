"""传输目录树扫描 —— 全量传输（scp -r 语义，不过滤隐藏/忽略项）

单文件场景返回单条目（relpath=""），目录场景递归整棵树（含空目录）。
条目上限 TRANSFER_MAX_FILES 防御（超出报错，不做分帧重组）。
"""

import os
from typing import List

from ..config.transfer import TRANSFER_MAX_FILES
from .common import ENTRY_DIR, ENTRY_FILE, TransferError, entry


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
    count = 1
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        for name in dirnames:
            rel = name if rel_dir == "." else rel_dir + "/" + name
            entries.append(entry(rel, ENTRY_DIR))
            count += 1
            if count > TRANSFER_MAX_FILES:
                raise TransferError(
                    "too many entries (> %d); split into smaller batches"
                    % TRANSFER_MAX_FILES
                )
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = name if rel_dir == "." else rel_dir + "/" + name
            st = os.stat(full)
            entries.append(entry(rel, ENTRY_FILE, st.st_size, st.st_mtime))
            count += 1
            if count > TRANSFER_MAX_FILES:
                raise TransferError(
                    "too many entries (> %d); split into smaller batches"
                    % TRANSFER_MAX_FILES
                )
    return entries
