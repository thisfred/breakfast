from ast import AST
from dataclasses import dataclass
from typing import Protocol


@dataclass(order=True, frozen=True)
class Position(Protocol):
    source: "Source"
    row: int
    column: int

    def __add__(self, column_offset: int) -> "Position":
        ...


class Source(Protocol):
    path: str
    lines: tuple[str] | None
    module_name: str
    guaranteed_lines: tuple[str]

    def position(self, row: int, column: int) -> Position:
        ...

    def find_after(self, name: str, position: Position) -> Position:
        ...

    def get_ast(self) -> AST:
        ...
