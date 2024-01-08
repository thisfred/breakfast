from ast import AST
from dataclasses import dataclass
from typing import Protocol


@dataclass(order=True, frozen=True)
class Position(Protocol):
    source: "Source"
    row: int
    column: int

    def __add__(self, other: int, /) -> "Position":
        ...

    def next_line(self) -> "Position":
        ...


class Source(Protocol):
    @property
    def path(self) -> str:
        ...

    @property
    def guaranteed_lines(self) -> tuple[str, ...]:
        ...

    @property
    def module_name(self) -> str:
        ...

    def position(self, row: int, column: int) -> Position:
        ...

    def find_after(self, name: str, position: Position) -> Position:
        ...

    def get_string_starting_at(self, position: Position) -> str:
        ...

    def get_ast(self) -> AST:
        ...
