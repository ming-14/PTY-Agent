"""插件权限单测 — 能力集合、require/check、审计拒绝"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.permissions import PermissionChecker, PermissionDenied  # noqa: E402


class TestPermissionChecker:
    def test_required_granted(self):
        checker = PermissionChecker("demo", ["filesystem.read", "filesystem.write"], [], [])
        assert checker.check("filesystem.read") is True
        assert checker.check("network.connect") is False
        assert checker.granted() == ["filesystem.read", "filesystem.write"]

    def test_policy_grant_and_deny(self):
        checker = PermissionChecker(
            "demo", ["filesystem.read"], ["network.connect"], ["filesystem.read"]
        )
        assert checker.check("filesystem.read") is False
        assert checker.check("network.connect") is True

    def test_require_ok(self):
        PermissionChecker("demo", ["filesystem.read"], [], []).require("filesystem.read")

    def test_require_denied_raises(self):
        checker = PermissionChecker("demo", [], [], [])
        with pytest.raises(PermissionDenied):
            checker.require("filesystem.write", resource="/etc/passwd")

    def test_require_denied_message(self):
        checker = PermissionChecker("demo", [], [], [])
        with pytest.raises(PermissionDenied) as exc:
            checker.require("filesystem.write", resource="/etc/passwd")
        assert "demo" in str(exc.value)
        assert "/etc/passwd" in str(exc.value)
