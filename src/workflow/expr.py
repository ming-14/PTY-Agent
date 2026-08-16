"""Workflow 安全表达式求值 — AST 白名单

供 if 条件与 {{...}} 插值使用。只允许字面量/名称/属性/下标/容器、
比较/布尔/算术运算，拒绝一切调用与属性方法执行（无副作用、无文件/
网络访问），防止不可信定义文件注入任意代码。

名称解析按字典键名直取：vars.foo → ns["vars"]["foo"]；
步骤结果直接以步骤 id 为名（build.reason → ns["build"]["reason"]）。
"""

import ast
import functools
import re
from typing import Any, Dict

_INTERP_RE = re.compile(r"\{\{(.*?)\}\}")

# 小写字面量别名：YAML/JSON 习惯写法（Python AST 将 true/false/null/none 解析为 Name，
# 若不做别名会因"未知的名称"求值失败，导致 if:"false" 步骤 FAILED 而非 skipped）
_LITERAL_ALIASES = {"true": True, "false": False, "null": None, "none": None}

# AST 节点白名单：不含 Call、JoinedStr（f-string）、推导式、Lambda、Starred 等
_ALLOWED_NODES = (
    ast.Expression,
    ast.Name,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.Compare,
    ast.BoolOp,
    ast.UnaryOp,
    ast.BinOp,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Load,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
)


class ExpressionError(ValueError):
    """表达式语法或求值错误（白名单拒绝/名称缺失/类型不符）"""


def _check_node(node: ast.AST) -> None:
    if not isinstance(node, _ALLOWED_NODES):
        raise ExpressionError(
            "表达式包含不允许的语法: %s" % type(node).__name__
        )


def _eval(node: ast.AST, ns: Dict[str, Any]) -> Any:
    _check_node(node)
    if isinstance(node, ast.Expression):
        return _eval(node.body, ns)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        # 命名空间优先（防御与步骤 id 撞名），未命中再尝试小写字面量别名
        if node.id in ns:
            return ns[node.id]
        if node.id in _LITERAL_ALIASES:
            return _LITERAL_ALIASES[node.id]
        raise ExpressionError("未知的名称: %s" % node.id)
    if isinstance(node, ast.Attribute):
        obj = _eval(node.value, ns)
        # dict 属性访问等价下标（vars.foo / steps.x.output）；其余对象走 getattr
        if isinstance(obj, dict):
            if node.attr not in obj:
                raise ExpressionError("对象没有属性 %s" % node.attr)
            return obj[node.attr]
        if not hasattr(obj, node.attr):
            raise ExpressionError(
                "对象没有属性 %s.%s" % (type(obj).__name__, node.attr)
            )
        return getattr(obj, node.attr)
    if isinstance(node, ast.Subscript):
        obj = _eval(node.value, ns)
        key = _eval(node.slice, ns)
        try:
            return obj[key]
        except (KeyError, IndexError, TypeError) as e:
            raise ExpressionError("下标访问失败: %s" % e) from e
    if isinstance(node, (ast.List, ast.Tuple)):
        items = [_eval(elt, ns) for elt in node.elts]
        return items if isinstance(node, ast.List) else tuple(items)
    if isinstance(node, ast.Dict):
        return {
            _eval(k, ns): _eval(v, ns) for k, v in zip(node.keys, node.values)
        }
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ns)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, ns)
            if not _eval_compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for value in node.values:
                if not _eval(value, ns):
                    return False
            return True
        for value in node.values:
            if _eval(value, ns):
                return True
        return False
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, ns)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, ns)
        right = _eval(node.right, ns)
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            return left % right
        except TypeError as e:
            raise ExpressionError("算术运算失败: %s" % e) from e
    raise ExpressionError("不支持的表达式节点: %s" % type(node).__name__)


def _eval_compare(op: ast.cmpop, left, right) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.In):
        return left in right
    return left not in right


@functools.lru_cache(maxsize=256)
def _parse_expr(source: str) -> ast.AST:
    """解析表达式为 AST（缓存：同一表达式在多步骤/多次求值间复用）"""
    return ast.parse(source, mode="eval")


def eval_expr(source: str, ns: Dict[str, Any]) -> Any:
    """求值表达式字符串（AST 白名单），返回求值结果

    Raises:
        ExpressionError: 语法错误 / 白名单拒绝 / 名称缺失
    """
    if not isinstance(source, str):
        raise ExpressionError("表达式必须是字符串")
    try:
        tree = _parse_expr(source)
    except SyntaxError as e:
        raise ExpressionError("表达式语法错误: %s" % e) from e
    return _eval(tree, ns)


def render_text(text: str, ns: Dict[str, Any]) -> str:
    """把文本中的 {{expr}} 占位符替换为表达式求值结果字符串

    无占位符时原样返回；求值异常的占位符抛 ExpressionError（定义期暴露）。
    """
    if "{{" not in text:
        return text

    def _replace(match) -> str:
        value = eval_expr(match.group(1).strip(), ns)
        return str(value)

    return _INTERP_RE.sub(_replace, text)


def render_value(value, ns: Dict[str, Any]):
    """递归渲染 dict/list/str 中的 {{expr}} 占位符（非 str 原样保留）"""
    if isinstance(value, str):
        return render_text(value, ns)
    if isinstance(value, dict):
        return {k: render_value(v, ns) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, ns) for v in value]
    return value