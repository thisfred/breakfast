"""
These types are part of the breakfast api, and should not be changed without taking
great care not to break backwards compatibility. Adding fields and methods should be
fine (though the smaller the API the easier it is to maintain.) Changing types or
signatures of existing fields or methods is not.
"""

import ast
from ast import AST
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol


class Sentinel(Enum):
    token = 0


DEFAULT = Sentinel.token


class NotFoundError(Exception):
    pass


class Ranged(Protocol):  # pragma: nocover
    @property
    def start(self) -> "Position": ...

    @property
    def end(self) -> "Position": ...

    @property
    def source(self) -> "Source": ...

    def __contains__(self, other: "Ranged") -> bool: ...


@dataclass(order=True, frozen=True)  # pragma: nocover
class Position(Protocol):
    source: "Source"
    row: int
    column: int

    @property
    def start(self) -> "Position": ...

    @property
    def end(self) -> "Position": ...

    @property
    def start_of_line(self) -> "Position": ...

    @property
    def line(self) -> "Line": ...

    @property
    def level(self) -> int: ...

    @property
    def as_range(self) -> "TextRange": ...

    def __contains__(self, other: "Ranged") -> bool: ...
    def __add__(self, other: int, /) -> "Position": ...
    def __sub__(self, to_subtract: int, /) -> "Position": ...
    def through(self, end: "Position") -> "TextRange": ...
    def to(self, end: "Position") -> "TextRange": ...
    def insert(self, text: str) -> "Edit": ...


def contains(self: Ranged, other: Ranged) -> bool:
    return (
        self.source == other.source
        and self.start <= other.start
        and self.end >= other.end
    )


@dataclass(order=True, frozen=True)  # pragma: nocover
class TextRange(Protocol):
    start: Position
    end: Position

    @property
    def source(self) -> "Source": ...

    @property
    def text(self) -> str: ...

    @property
    def names(self) -> Sequence["Occurrence"]: ...

    @property
    def definitions(self) -> Sequence["Occurrence"]: ...

    @property
    def enclosing_scopes(
        self,
    ) -> Sequence["ScopeWithRange"]: ...

    @property
    def enclosing_scope(self) -> "ScopeWithRange": ...

    @property
    def enclosing_nodes(self) -> Sequence["NodeWithRange[ast.AST]"]: ...

    @property
    def enclosing_call(self) -> "NodeWithRange[ast.Call] | None": ...

    @property
    def enclosing_assignment(self) -> "NodeWithRange[ast.Assign] | None": ...

    @property
    def statements(self) -> Iterable[ast.stmt]: ...

    @property
    def expression(self) -> ast.expr | None: ...

    def __contains__(self, other: "Ranged") -> bool: ...

    def enclosing_nodes_by_type[T: ast.AST](
        self, node_type: type[T]
    ) -> Sequence["NodeWithRange[T]"]: ...

    def text_with_substitutions(
        self, substitutions: Sequence["Edit"]
    ) -> Sequence[str]: ...

    def replace(self, new_text: str) -> "Edit": ...


@dataclass(frozen=True)
class NodeWithRange[T: ast.AST]:
    node: T
    range: "TextRange"

    @property
    def start(self) -> Position:
        return self.range.start

    @property
    def end(self) -> Position:
        return self.range.end

    @property
    def source(self) -> "Source":
        return self.range.source

    def __contains__(self, other: "Ranged") -> bool:
        return contains(self, other)


ScopeWithRange = (
    NodeWithRange[ast.FunctionDef]
    | NodeWithRange[ast.AsyncFunctionDef]
    | NodeWithRange[ast.ClassDef]
    | NodeWithRange[ast.Module]
)


class Line(Protocol):
    @property
    def row(self) -> int: ...

    @property
    def start(self) -> "Position": ...

    @property
    def end(self) -> "Position": ...

    @property
    def text(self) -> str: ...

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
    def module_name(self) -> tuple[str, ...]: ...

    @property
    def ast(self) -> AST: ...

    def position(self, row: int, column: int) -> Position: ...

    def find_after(self, name: str, position: Position) -> Position: ...

    def get_string_starting_at(self, position: Position) -> str: ...

    def get_text(self, *, start: Position, end: Position) -> str: ...

    def get_name_at(self, position: Position) -> str | None: ...

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

    @property
    def source(self) -> "Source":
        return self.text_range.source


class NodeType(Enum):
    SCOPE = auto()
    MODULE_SCOPE = auto()
    DEFINITION = auto()
    REFERENCE = auto()
    INSTANCE = auto()
    CLASS = auto()


class Occurrence(Protocol):  # pragma: nocover
    @property
    def name(self) -> str: ...

    @property
    def position(self) -> Position: ...

    @property
    def ast(self) -> ast.AST | None: ...

    @property
    def node_type(self) -> NodeType: ...
