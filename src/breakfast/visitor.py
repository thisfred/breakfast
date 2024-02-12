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
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for node in value:
                if isinstance(node, ast.AST):
                    yield from f(node, *args, **kwargs)
        elif isinstance(value, ast.AST):
            yield from f(value, *args, **kwargs)
