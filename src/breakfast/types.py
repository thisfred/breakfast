"""
These types are part of the breakfast api, and should not be changed without taking
great care not to break backwards compatibility. Adding fields and methods should be
fine. Changing types or signatures of existing fields or methods is not.
"""

import ast
from ast import AST
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
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
    def level(self) -> int: ...

    @property
    def as_range(self) -> "TextRange": ...

    def through(self, end: "Position") -> "TextRange": ...

    def to(self, end: "Position") -> "TextRange": ...

    def insert(self, text: str) -> "Edit": ...


@dataclass(order=True, frozen=True)  # pragma: nocover
class TextRange(Protocol):
    start: Position
    end: Position

    def __contains__(
        self, position_or_range: "Position | TextRange"
    ) -> bool: ...

    @property
    def text(self) -> str: ...

    @property
    def names(self) -> Sequence["Occurrence"]: ...

    @property
    def definitions(self) -> Sequence["Occurrence"]: ...

    @property
    def source(self) -> "Source": ...

    @property
    def enclosing_scopes(
        self,
    ) -> Sequence["ScopeWithRange"]: ...

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


ScopeWithRange = (
    NodeWithRange[ast.FunctionDef]
    | NodeWithRange[ast.AsyncFunctionDef]
    | NodeWithRange[ast.ClassDef]
    | NodeWithRange[ast.Module]
)


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


class NodeType(Enum):
    SCOPE = auto()
    MODULE_SCOPE = auto()
    DEFINITION = auto()
    REFERENCE = auto()
    INSTANCE = auto()
    CLASS = auto()


@dataclass(frozen=True)
class Occurrence:
    name: str
    position: Position
    ast: ast.AST | None
    node_type: NodeType
