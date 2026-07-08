"""A tiny arithmetic evaluator for PAL-style tool offload.

PAL's insight: a small model that can't reliably *compute* can reliably *write
down the computation*. The harness then evaluates that program exactly. This
evaluator accepts pure arithmetic expressions only — it parses to an AST and
walks a whitelist of node types, so there is no way to reference names, call
functions, or touch anything outside numbers and operators.
"""
from __future__ import annotations

import ast
import operator
from typing import Union

Number = Union[int, float]

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_MAX_POW_EXPONENT = 1000  # keep 9**9**9 from eating the process


class SandboxError(ValueError):
    """The expression is not pure arithmetic (or is unreasonable to compute)."""


def _eval_node(node: ast.AST) -> Number:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise SandboxError(f"non-numeric constant: {node.value!r}")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise SandboxError(f"operator not allowed: {type(node.op).__name__}")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW_EXPONENT:
            raise SandboxError(f"exponent too large: {right}")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise SandboxError(f"unary operator not allowed: {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    raise SandboxError(f"syntax not allowed: {type(node).__name__}")


def eval_arithmetic(expression: str) -> Number:
    """Evaluate a pure arithmetic expression exactly. Raises SandboxError for
    anything that isn't numbers and operators."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise SandboxError(f"not a valid expression: {exc}") from exc
    return _eval_node(tree)
