"""权限检查 —— D3 已定：当前仅保留判断接口，直接放行

后续呈现层（前端弹窗/会话级记住）只需替换 check 实现，
writer 的调用点不变（design §4.6）。

路径边界判定（path == root or startswith(root + os.sep)）同样保留在
writer 内，与状态机检查一起；本期不实现边界限制，待呈现层落地时挂接。
"""


class PermissionPolicy:
    """文件操作权限检查器（后台策略，暂不呈现 CLI）"""

    def check(self, action: str, path: str) -> bool:
        """检查某操作是否被允许；当前一律放行，返回 True"""
        return True