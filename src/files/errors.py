"""文件工具异常类型 —— 供 handler 层区分错误语义与用户提示"""


class FileToolError(Exception):
    """文件工具基类异常，message 即为用户可见的错误提示"""


class FileReadRequiredError(FileToolError):
    """写操作前置检查失败：文件未被读取过，或已被外部修改

    writer 在检查到 modTime 晚于 lastRead 或从未读时抛出，
    handler 捕获后返回 Response.error(message)。
    """


class FilePermissionDeniedError(FileToolError):
    """权限检查拒绝写操作"""