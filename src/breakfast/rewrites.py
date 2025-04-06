import ast
import logging
from collections.abc import (
    Callable,
    Iterator,
)
from functools import singledispatch
from itertools import chain
from typing import Any

from breakfast.visitor import generic_transform

logger = logging.getLogger(__name__)

COMPARISONS: dict[type[ast.AST], Callable[[Any, Any], bool]] = {
    ast.Eq: lambda a, b: a == b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.Is: lambda a, b: a is b,
    ast.IsNot: lambda a, b: a is not b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.NotEq: lambda a, b: a != b,
    ast.NotIn: lambda a, b: a not in b,
}


@singledispatch
def substitute_nodes(
    node: ast.AST,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    if node in substitutions:
        yield from generic_transform(
            substitute_nodes, substitutions[node], substitutions
        )
    else:
        yield from generic_transform(substitute_nodes, node, substitutions)


@substitute_nodes.register
def substitute_nodes_in_name(
    node: ast.Name,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    substitution = substitutions.get(node)
    if substitution is None:
        yield node
    else:
        yield substitution


@substitute_nodes.register
def substitute_nodes_in_constant(
    node: ast.Constant,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    yield node


@substitute_nodes.register
def substitute_nodes_in_attribute(
    node: ast.Attribute,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    new_value = next(substitute_nodes(node.value, substitutions), None)
    if isinstance(new_value, ast.expr):
        yield ast.Attribute(new_value, attr=node.attr)
    else:
        yield node


@substitute_nodes.register
def substitute_nodes_in_if(
    node: ast.If,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    test = next(substitute_nodes(node.test, substitutions), None)
    body = (
        s
        for s in (
            chain.from_iterable(
                substitute_nodes(s, substitutions) for s in node.body
            )
        )
        if isinstance(s, ast.stmt)
    )
    orelse = (
        s
        for s in (
            chain.from_iterable(
                substitute_nodes(s, substitutions) for s in node.orelse
            )
        )
        if isinstance(s, ast.stmt)
    )
    if test:
        if always_true(test):
            yield from body
        elif always_false(test):
            yield from orelse
        else:
            if as_list := list(orelse):
                yield (
                    ast.If(
                        test=test,
                        body=list(body) or [ast.Pass()],
                        orelse=as_list,
                    )
                    if isinstance(test, ast.expr)
                    else node
                )
            elif as_list := list(body):
                yield (
                    ast.If(test=test, body=as_list, orelse=[])
                    if isinstance(test, ast.expr)
                    else node
                )


@substitute_nodes.register
def substitute_nodes_in_bool_op(
    node: ast.BoolOp,
    substitutions: dict[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    transformed = chain.from_iterable(
        substitute_nodes(value, substitutions) for value in node.values
    )
    if isinstance(node.op, ast.And):
        new_values = [
            v
            for v in transformed
            if not always_true(v) and isinstance(v, ast.expr)
        ]
        if len(new_values) == 1:
            yield new_values[0]
        else:
            yield (
                ast.BoolOp(op=ast.And(), values=new_values)
                if new_values
                else node
            )
    elif isinstance(node.op, ast.Or):
        new_values = [
            v
            for v in transformed
            if not always_false(v) and isinstance(v, ast.expr)
        ]
        if len(new_values) == 1:
            yield new_values[0]
        else:
            yield (
                ast.BoolOp(op=ast.Or(), values=new_values)
                if new_values
                else node
            )
    else:
        yield node


@singledispatch
def always_true(node: ast.AST) -> bool:
    return False


@always_true.register
def always_true_constant(node: ast.Constant) -> bool:
    return bool(node.value)


@always_true.register
def always_true_unary_op(node: ast.UnaryOp) -> bool:
    if isinstance(node.op, ast.Not):
        return always_false(node.operand)

    return False


@always_true.register
def always_true_compare(node: ast.Compare) -> bool:
    if not isinstance(node.left, ast.Constant):
        return False
    prev = node.left.value
    for op, comparator in zip(node.ops, node.comparators, strict=True):
        if not isinstance(comparator, ast.Constant):
            return False
        if not COMPARISONS[type(op)](prev, comparator.value):
            return False
        prev = comparator.value
    return True


@always_true.register
def always_true_bool_op(node: ast.BoolOp) -> bool:
    if isinstance(node.op, ast.Or):
        for value in node.values:
            if always_true(value):
                return True
        return False

    if isinstance(node.op, ast.And):
        for value in node.values:
            if not always_true(value):
                return False
        return True

    return False


@singledispatch
def always_false(node: ast.AST) -> bool:
    return False


@always_false.register
def always_false_unary_op(node: ast.UnaryOp) -> bool:
    if isinstance(node.op, ast.Not):
        return always_true(node.operand)

    return False


@always_false.register
def always_false_constant(node: ast.Constant) -> bool:
    return not bool(node.value)


@always_false.register
def always_false_compare(node: ast.Compare) -> bool:
    if not isinstance(node.left, ast.Constant):
        return False
    prev = node.left.value
    for op, comparator in zip(node.ops, node.comparators, strict=True):
        if not isinstance(comparator, ast.Constant):
            return False
        if COMPARISONS[type(op)](prev, comparator.value):
            return False
        prev = comparator.value
    return True


@always_false.register
def always_false_bool_op(node: ast.BoolOp) -> bool:
    if isinstance(node.op, ast.And):
        for value in node.values:
            if always_false(value):
                return True
        return False

    if isinstance(node.op, ast.Or):
        for value in node.values:
            if not always_false(value):
                return False
        return True

    return False
