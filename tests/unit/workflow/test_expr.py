"""workflow 表达式求值单元测试 — 小写字面量别名 / 基本求值语义"""

import pytest

from src.workflow.expr import ExpressionError, eval_expr


class TestLowercaseLiterals:
    """YAML/JSON 习惯的小写字面量应按字面量求值，而非"未知的名称"报错"""

    def test_true_false(self):
        assert eval_expr("true", {}) is True
        assert eval_expr("false", {}) is False

    def test_null_none(self):
        assert eval_expr("null", {}) is None
        assert eval_expr("none", {}) is None

    def test_lowercase_false_in_condition(self):
        """if: "false" 场景：求值结果为假（引擎据此 skipped，而非 FAILED）"""
        assert bool(eval_expr("false", {"vars": {}})) is False

    def test_used_in_bool_ops(self):
        assert eval_expr("false or vars.x", {"vars": {"x": 1}}) == 1
        assert eval_expr("true and vars.x > 0", {"vars": {"x": 2}}) is True

    def test_namespace_takes_precedence(self):
        """命名空间命中优先于别名（防御步骤 id 与字面量撞名）"""
        ns = {"false": 42}  # 假设存在 id 为 false 的步骤结果
        assert eval_expr("false", ns) == 42

    def test_unknown_name_still_errors(self):
        with pytest.raises(ExpressionError):
            eval_expr("whatever", {})


class TestBasicEval:
    """既有求值语义回归（别名改动不应破坏）"""

    def test_python_constants(self):
        assert eval_expr("True", {}) is True
        assert eval_expr("False", {}) is False
        assert eval_expr("None", {}) is None

    def test_attribute_access(self):
        ns = {"build": {"reason": "trigger_matched"}}
        assert eval_expr("build.reason == 'trigger_matched'", ns) is True
