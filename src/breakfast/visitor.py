from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from typing import Concatenate, ParamSpec, TypeVar

T = TypeVar("T")
P = ParamSpec("P")


def generic_visit(
    f: Callable[Concatenate[ast.AST, P], Iterator[T]],
    node: ast.AST,
    *args: P.args,
    **kwargs: P.kwargs,
) -> Iterator[T]:
    for _key, value in ast.iter_fields(node):
        if isinstance(value, list):
            for node in value:
                if isinstance(node, ast.AST):
                    yield from f(node, *args, **kwargs)
        elif isinstance(value, ast.AST):
            yield from f(value, *args, **kwargs)


def generic_transform(
    f: Callable[Concatenate[ast.AST, P], Iterator[ast.AST]],
    node: ast.AST,
    *args: P.args,
    **kwargs: P.kwargs,
) -> Iterator[ast.AST]:
    params: dict[str, ast.AST | list[ast.AST]] = {}
    for field, old_value in ast.iter_fields(node):
        if isinstance(old_value, list):
            new_values: list[ast.AST] = []
            for value in old_value:
                if isinstance(value, ast.AST):
                    for new_value in f(value, *args, **kwargs):
                        new_values.append(new_value)
                    continue
                new_values.append(value)
            params[field] = new_values
        elif isinstance(old_value, ast.AST):
            new_node = next(f(old_value, *args, **kwargs), None)
            if new_node is not None:
                params[field] = new_node
        else:
            params[field] = old_value
    yield node.__class__(**params)
