"""文件路径工具 —— 会话 cwd 解析/边界判定

相对路径解析约定：路径由命令处理层按 cwd_session 指定的会话 cwd 解析
（CLI 原样传输，跨机语义正确）。
"""

import os


# 记录 key 统一化：Windows 路径大小写不敏感，统一 normcase 避免同文件多 key
def normalize_key(path: str) -> str:
    """生成状态机/存储的 key（Windows 下统一小写）"""
    return os.path.normcase(path)


def resolve_session_path(path: str, session_cwd: str) -> str:
    """基于会话 cwd 解析路径（daemon 侧入口，跨机语义正确）

    路径基准是 PTY 会话创建时的工作目录（session.cwd）而非 CLI 侧：
    - ~ 按 daemon 用户展开（会话侧用户由会话进程自身决定，daemon 无法获知）
    - 绝对路径直接用（Windows 盘符 / Linux /）
    - 相对路径相对 session_cwd 拼接
    """
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.join(session_cwd, expanded))


def is_within(path: str, root: str) -> bool:
    """路径边界判定：path 在 root 内（或等于 root）

    使用 os.sep 边界而非字符串前缀，避免 "proj2" 被前缀误判为 "proj" 的问题。
    """
    norm_path = os.path.normpath(path)
    norm_root = os.path.normpath(root)
    return norm_path == norm_root or norm_path.startswith(norm_root + os.sep)
