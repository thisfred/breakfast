from __future__ import annotations

import ast
import logging
from collections import deque
from collections.abc import Callable, Container, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import singledispatch
from itertools import chain, repeat
from typing import Any

from breakfast.names import (
    all_occurrences,
)
from breakfast.types import Occurrence, Source, TextRange
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
    substitutions: Mapping[ast.AST, ast.AST],
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
    substitutions: Mapping[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    substitution = substitutions.get(node)
    if substitution is None:
        yield node
    else:
        yield substitution


@substitute_nodes.register
def substitute_nodes_in_constant(
    node: ast.Constant,
    substitutions: Mapping[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    yield node


@substitute_nodes.register
def substitute_nodes_in_attribute(
    node: ast.Attribute,
    substitutions: Mapping[ast.AST, ast.AST],
) -> Iterator[ast.AST]:
    new_value = next(substitute_nodes(node.value, substitutions), None)
    if isinstance(new_value, ast.expr):
        yield ast.Attribute(new_value, attr=node.attr)
    else:
        yield node


@substitute_nodes.register
def substitute_nodes_in_if(
    node: ast.If,
    substitutions: Mapping[ast.AST, ast.AST],
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
    substitutions: Mapping[ast.AST, ast.AST],
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


@dataclass(kw_only=True)
class ArgumentMapper:
    arguments: ast.arguments
    body_range: TextRange
    returned_names: Container[str]
    sources: Sequence[Source]
    static_method: bool

    def get_occurrences(
        self, argument: ast.keyword | ast.arg, body_range: TextRange
    ) -> Sequence[Occurrence]:
        if argument.arg is None:
            return []
        arg_position = self.body_range.start.source.node_position(argument)
        return [
            o
            for o in all_occurrences(arg_position, sources=self.sources)
            if o.position in body_range and o.ast
        ]

    def substitute_argument(
        self,
        argument: ast.keyword | ast.arg,
        value: ast.AST,
        substitutions: dict[ast.AST, ast.AST],
    ) -> None:
        occurrences = self.get_occurrences(argument, self.body_range)
        if not (
            argument.arg in self.returned_names
            or all(not o.is_definition for o in occurrences)
        ):
            return

        for occurrence in occurrences:
            if isinstance(occurrence.ast, ast.Name):
                substitutions[occurrence.ast] = value

    def add_substitutions(
        self, call: ast.Call, substitutions: dict[ast.AST, ast.AST]
    ) -> None:
        call_args = deque(call.args)
        if isinstance(call.func, ast.Attribute) and not self.static_method:
            call_args.appendleft(call.func.value)
        call_keywords = {k.arg: k.value for k in call.keywords}
        self.substitute_position_only_arguments(
            substitutions=substitutions, call_args=call_args
        )
        self.substitute_arguments(
            substitutions=substitutions,
            call_args=call_args,
            call_keywords=call_keywords,
        )
        self.substitute_vararg(substitutions=substitutions, call_args=call_args)
        self.substitute_keyword_only_arguments(
            substitutions=substitutions, call_keywords=call_keywords
        )
        self.substitute_kwarg(call=call, substitutions=substitutions)

    def substitute_kwarg(
        self, call: ast.Call, substitutions: dict[(ast.AST, ast.AST)]
    ) -> None:
        if self.arguments.kwarg:
            self.substitute_argument(
                self.arguments.kwarg,
                ast.Dict(
                    keys=[
                        ast.Constant(value=k.arg)
                        for k in call.keywords
                        if k.arg
                    ],
                    values=[k.value for k in call.keywords],
                ),
                substitutions,
            )

    def substitute_keyword_only_arguments(
        self,
        substitutions: dict[(ast.AST, ast.AST)],
        call_keywords: dict[str | None, ast.expr],
    ) -> None:
        defaults = (
            *repeat(
                None,
                len(self.arguments.kwonlyargs)
                - len(self.arguments.kw_defaults),
            ),
            *self.arguments.kw_defaults,
        )
        for arg, default in zip(
            self.arguments.kwonlyargs, defaults, strict=True
        ):
            if arg.arg in call_keywords:
                substitutions[arg] = call_keywords.pop(arg.arg)
                self.substitute_argument(
                    arg, call_keywords.pop(arg.arg), substitutions
                )
            elif isinstance(default, ast.expr):
                substitutions[arg] = default
                self.substitute_argument(arg, default, substitutions)

    def substitute_vararg(
        self,
        substitutions: dict[(ast.AST, ast.AST)],
        call_args: deque[ast.expr],
    ) -> None:
        if self.arguments.vararg:
            self.substitute_argument(
                self.arguments.vararg,
                ast.Tuple(elts=list(call_args)),
                substitutions,
            )

    def substitute_arguments(
        self,
        substitutions: dict[(ast.AST, ast.AST)],
        call_args: deque[ast.expr],
        call_keywords: dict[str | None, ast.expr],
    ) -> None:
        for arg in self.arguments.args:
            if call_args:
                self.substitute_argument(
                    arg, call_args.popleft(), substitutions
                )
            elif call_keywords and arg.arg in call_keywords:
                self.substitute_argument(
                    arg, call_keywords.pop(arg.arg), substitutions
                )

    def substitute_position_only_arguments(
        self,
        substitutions: dict[(ast.AST, ast.AST)],
        call_args: deque[ast.expr],
    ) -> None:
        for arg in self.arguments.posonlyargs:
            if call_args:
                self.substitute_argument(
                    arg, call_args.popleft(), substitutions
                )


def rewrite_body(
    function_definition: ast.FunctionDef | ast.AsyncFunctionDef,
    substitutions: dict[ast.AST, ast.AST],
) -> list[ast.stmt]:
    return [
        s
        for node in function_definition.body
        for s in substitute_nodes(node, substitutions)
        if isinstance(s, ast.stmt)
    ]
