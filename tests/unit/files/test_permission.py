"""权限策略单元测试 —— D3 放行语义与接口稳定性"""

from src.files.permission import PermissionPolicy


class TestPermissionPolicy:
    def test_check_always_allows(self):
        policy = PermissionPolicy()
        assert policy.check("write", "C:/anywhere/file.txt") is True

    def test_check_interface_signature(self):
        # 呈现层替换实现时依赖的接口契约：action + path 两个参数
        policy = PermissionPolicy()
        assert callable(policy.check)
        assert policy.check("edit", "file.txt") is True