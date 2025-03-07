"""
These types are part of the breakfast api, and should not be changed without taking
great care not to break backwards compatibility. Adding fields and methods should be
fine. Changing types or signatures of existing fields or methods is not.
"""

import ast
from ast import AST
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class NotFoundError(Exception):
    pass


@dataclass(order=True, frozen=True)  # pragma: nocover
class Position(Protocol):
    source: "Source"
    row: int
    column: int

    def __add__(self, other: int, /) -> "Position": ...

    def __sub__(self, to_subtract: int, /) -> "Position": ...

    @property
    def start_of_line(self) -> "Position": ...

    @property
    def line(self) -> "Line": ...

    @property
    def body_for_callable(self) -> "TextRange | None": ...

    @property
    def indentation(self) -> str: ...

    def through(self, end: "Position") -> "TextRange": ...

    def insert(self, text: str) -> "Edit": ...


@dataclass(order=True, frozen=True)  # pragma: nocover
class TextRange(Protocol):
    start: Position
    end: Position

    @property
    def text(self) -> str: ...

    @property
    def names(self) -> Sequence[tuple[str, Position, ast.expr_context]]: ...

    @property
    def definitions(self) -> list[tuple[str, "Position"]]: ...

    @property
    def source(self) -> "Source": ...

    @property
    def enclosing_scopes(
        self,
    ) -> Sequence[
        tuple[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, "TextRange"]
    ]: ...

    def enclosing_nodes_by_type[T: ast.AST](
        self, node_type: type[T]
    ) -> Sequence[tuple[T, "TextRange"]]: ...

    @property
    def enclosing_nodes(self) -> Sequence[tuple[ast.AST, "TextRange"]]: ...

    @property
    def enclosing_call(self) -> tuple[ast.Call, "TextRange"] | None: ...

    @property
    def enclosing_assignment(self) -> tuple[ast.Assign, "TextRange"] | None: ...

    def text_with_substitutions(
        self, substitutions: Sequence[tuple["TextRange", str]]
    ) -> Sequence[str]: ...

    def __contains__(self, position_or_range: "Position | TextRange") -> bool: ...


@dataclass(order=True, frozen=True)  # pragma: nocover
class Line(Protocol):
    source: "Source"
    row: int

    @property
    def text(self) -> str: ...

    @property
    def start(self) -> Position: ...

    @property
    def end(self) -> Position: ...

    @property
    def next(self) -> "Line | None": ...

    @property
    def previous(self) -> "Line | None": ...


class Source(Protocol):  # pragma: nocover
    @property
    def path(self) -> str: ...

    @property
    def lines(self) -> tuple[Line, ...]: ...

    @property
    def text(self) -> tuple[str, ...]: ...

    @property
    def module_name(self) -> str: ...

    @property
    def ast(self) -> AST: ...

    def position(self, row: int, column: int) -> Position: ...

    def find_after(self, name: str, position: Position) -> Position: ...

    def get_string_starting_at(self, position: Position) -> str: ...

    def get_text(self, *, start: Position, end: Position) -> str: ...

    def get_name_at(self, position: Position) -> str: ...

    def node_position(self, node: AST) -> Position: ...

    def node_end_position(self, node: AST) -> Position | None: ...

    def node_range(self, node: AST) -> TextRange | None: ...


@dataclass(order=True, frozen=True)
class Edit:
    text_range: TextRange
    text: str

    @property
    def start(self) -> Position:
        return self.text_range.start

    @property
    def end(self) -> Position:
        return self.text_range.end
