"""权限检查 —— 当前一律放行

提供 check 判定接口供 write/edit 流程调用，替换实现不影响调用点。
路径边界判定与状态机检查位于 writer 内。
"""


class PermissionPolicy:
    """文件操作权限检查器（后台策略，暂不呈现 CLI）"""

    def check(self, action: str, path: str) -> bool:
        """检查某操作是否被允许；当前一律放行，返回 True"""
        return True
