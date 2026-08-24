"""插件权限 — 基于能力的声明式检查 + 审计

有效权限 = 清单 permissions.required ∪ policy.grant − policy.deny。
policy.json（config/plugins/policy.json）由管理员维护，按插件 id 追加授予/拒绝。
插件经 ctx.permission.require/check 自行检查；拒绝事件写入日志（审计轨迹）。
"""

from typing import List, Optional

from ..logging import get_logger

_logger = get_logger("pty-plugins")


class PermissionDenied(Exception):
    """权限不足（require 检查未通过）"""


class PermissionChecker:
    """插件能力检查器（加载时由环境构建，不可变）"""

    def __init__(
        self,
        plugin_id: str,
        required: List[str],
        granted: List[str],
        denied: List[str],
    ):
        self._plugin_id = plugin_id
        # denied 覆盖全部（含 required）：须显式括号，'-' 优先级高于 '|'
        self._granted = sorted((set(required) | set(granted)) - set(denied))

    def granted(self) -> List[str]:
        """当前有效权限列表（供 plugin info 展示）"""
        return list(self._granted)

    def check(self, permission: str) -> bool:
        return permission in self._granted

    def require(self, permission: str, resource: Optional[str] = None) -> None:
        """检查权限并记录审计日志；不满足抛 PermissionDenied"""
        if permission in self._granted:
            return
        _logger.warning(
            "权限拒绝 plugin=%s permission=%s resource=%s",
            self._plugin_id,
            permission,
            resource or "-",
        )
        suffix = " (resource: %s)" % resource if resource else ""
        raise PermissionDenied(
            "插件 %s 无权限 %s%s" % (self._plugin_id, permission, suffix)
        )