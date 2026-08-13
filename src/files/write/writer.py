"""file write / file edit 用例 —— 覆盖写/新建/唯一匹配替换（仿 opencode write.go / edit.go）

内部顺序（design §4.3）：
1. 状态机检查（已存在文件）：未读过或 modTime > lastRead → FileReadRequiredError
2. 内容相同（新内容 == 磁盘内容）→ FileToolError
3. generate_diff（供权限与日志，本期不进 CLI 响应）
4. PermissionPolicy.check → 拒绝时 FilePermissionDeniedError
5. os.makedirs(父目录) + 原样落盘（newline="" 防换行翻译）
6. 历史版本链：
   - GetLatest 失败（无历史）→ Create(旧内容) 落 initial，跳过中间版本判定
     （修正 opencode write.go:200 首次写入冗余存两份相同旧内容的 bug）
   - 历史最新内容 ≠ 磁盘内容 → CreateVersion(旧内容)（用户手改的中间版本）
   - 再 CreateVersion(新内容)
7. 状态机双刷：record_write + record_read（工具自身已知最新内容）

file edit 三分支（design §4.3，与 opencode edit.go 相同）：
- `--old` 空 = create：文件必须不存在，直接写 `new`
- `--new` 空 = delete：old 在磁盘内容中唯一匹配后删除
- 均非空 = replace：old 唯一匹配（str.find == str.rfind）后替换为 new
- 唯一匹配校验失败 / old 未找到 → FileToolError，不落盘
"""

import logging
import os
from typing import Optional

from ...config.files import MAX_CONTENT_LEN
from ..diff import generate_diff
from ..errors import FileToolError, FileReadRequiredError, FilePermissionDeniedError
from ..history import FileHistoryStore
from ..permission import PermissionPolicy
from ..state import FileRecordStore, get_default_store

_logger = logging.getLogger("pty-daemon")


class WriteResult:
    """写入结果：path、是否已存在、diff 统计（供日志，不进 CLI 响应）"""

    __slots__ = ("path", "existed", "additions", "removals")

    def __init__(self, path: str, existed: bool, additions: int, removals: int):
        self.path = path
        self.existed = existed
        self.additions = additions
        self.removals = removals


def _check_content_size(content: str) -> None:
    if len(content) > MAX_CONTENT_LEN:
        raise FileToolError(
            "Content is too large (%d bytes). Maximum size is %d bytes"
            % (len(content), MAX_CONTENT_LEN)
        )


def _require_up_to_date(path: str, last_read: Optional[float]) -> None:
    """已存在文件的冲突检查：未读过或外部修改过 → 拒绝（read-before-write）"""
    if last_read is None:
        raise FileReadRequiredError(
            "Last read is 0 seconds ago; file must be read first: %s" % path
        )
    mod_time = os.path.getmtime(path)
    if mod_time > last_read:
        raise FileReadRequiredError(
            "File has been modified since last read; use file read to update: %s" % path
        )


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _apply_unique_replace(content: str, old: str, replacement: str) -> str:
    """old 必须唯一出现：未找到或非唯一 → FileToolError，不落盘"""
    first = content.find(old)
    if first < 0:
        raise FileToolError("Old string not found in file: %r" % old)
    if content.rfind(old) != first:
        raise FileToolError(
            "Old string is not unique (%d matches); specify more context: %r"
            % (content.count(old), old)
        )
    return content[:first] + replacement + content[first + len(old):]


def _commit_write(
    path: str,
    content: str,
    action: str,
    store: FileRecordStore,
    history: FileHistoryStore,
    policy: PermissionPolicy,
) -> WriteResult:
    """公共提交路径（write/edit 共用）：读旧内容 → diff → 权限 → 落盘 → 历史 → 双刷

    内容相同拒绝只针对已存在文件（写空文件到新路径是合法操作）。
    """
    _check_content_size(content)
    existed = os.path.exists(path)
    if existed:
        old_content = _read_text(path)
        if old_content == content:
            raise FileToolError(
                "No changes to write: file already contains the given content: %s" % path
            )
    else:
        old_content = ""

    # diff 供权限记录与日志（本期不进 CLI 响应）
    diff_text, additions, removals = generate_diff(old_content, content, path)
    if not policy.check(action, path):
        raise FilePermissionDeniedError(
            "Permission denied to %s: %s" % (action, path)
        )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    _logger.info(
        "file_%s: path=%s existed=%s additions=%d removals=%d diff=%d chars",
        action, path, existed, additions, removals, len(diff_text),
    )

    # 历史版本链（修正 opencode 首次写入冗余版本的 bug）
    latest = history.get_latest(path)
    if latest is None:
        history.create(path, old_content)
    elif latest["content"] != old_content:
        history.create_version(path, old_content)
    history.create_version(path, content)

    # 状态机双刷：工具自身已知最新内容。
    # readTime 以文件自身 mtime 为基准（见 state.record_read），
    # 使紧邻的自写操作不被误判为外部冲突，外部修改仍能检测
    store.record_write(path)
    store.record_read(path, os.path.getmtime(path))
    return WriteResult(path=path, existed=existed, additions=additions, removals=removals)


def write_file(
    path: str,
    content: str,
    *,
    store: Optional[FileRecordStore] = None,
    history: Optional[FileHistoryStore] = None,
    policy: Optional[PermissionPolicy] = None,
) -> WriteResult:
    """覆盖写/新建文件（自动建父目录）

    Args:
        path: 绝对路径（CLI 侧已解析）
        content: 新内容（允许空串）
        store/history/policy: 依赖注入（默认 daemon 单例/默认历史库/放行策略）

    Raises:
        FileReadRequiredError: 已存在文件未经读取或已被外部修改
        FilePermissionDeniedError: 权限检查拒绝
        FileToolError: 内容超限/内容相同等
        OSError: 落盘 IO 错误
    """
    store = store or get_default_store()
    history = history or FileHistoryStore()
    policy = policy or PermissionPolicy()

    if os.path.exists(path):
        # 状态机检查：未读过或外部修改 → 拒绝，提示先 file read
        _require_up_to_date(path, store.last_read(path))
    return _commit_write(path, content, "write", store, history, policy)


def edit_file(
    path: str,
    old: str,
    new: str,
    *,
    store: Optional[FileRecordStore] = None,
    history: Optional[FileHistoryStore] = None,
    policy: Optional[PermissionPolicy] = None,
) -> WriteResult:
    """唯一匹配替换（old/new 均非空）、删除（new 为空）、新建（old 为空）

    三分支与 opencode edit.go 相同（design §4.3）：
    - create：文件必须不存在
    - replace/delete：文件必须存在且已被读取、未被外部修改；old 必须唯一

    Raises:
        FileReadRequiredError: replace/delete 前置检查失败
        FilePermissionDeniedError: 权限检查拒绝
        FileToolError: old 未找到/非唯一/文件状态不符等
        OSError: 落盘 IO 错误
    """
    store = store or get_default_store()
    history = history or FileHistoryStore()
    policy = policy or PermissionPolicy()

    if not old and not new:
        raise FileToolError("old and new are both empty: nothing to do")

    if not old:
        # create 分支：文件必须不存在
        if os.path.exists(path):
            raise FileToolError("File already exists: %s" % path)
        return _commit_write(path, new, "edit-create", store, history, policy)

    if not os.path.exists(path):
        raise FileToolError("File does not exist: %s" % path)
    # replace/delete：未读过或外部修改 → 拒绝
    _require_up_to_date(path, store.last_read(path))
    content = _read_text(path)
    new_content = _apply_unique_replace(content, old, new)
    return _commit_write(path, new_content, "edit", store, history, policy)