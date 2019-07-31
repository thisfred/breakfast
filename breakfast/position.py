from ast import AST
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Optional


if TYPE_CHECKING:
    from breakfast.source import Source


class IllegalPosition(Exception):
    pass


@dataclass(order=True, frozen=True)
class Position:
    source: "Source"
    row: int
    column: int
    node: Optional[AST] = None

    def __post_init__(self) -> None:
        if self.column < 0:
            raise IllegalPosition(f"Illegal value for column: {self.column}.")
        if self.row < 0:
            raise IllegalPosition(f"Illegal value for row: {self.row}.")

    def __add__(self, column_offset: int) -> "Position":
        return self._add_offset(column_offset)

    def __sub__(self, column_offset: int) -> "Position":
        new_offset = self.column - column_offset
        if new_offset < 0:
            raise IllegalPosition()

        return self._add_offset(-column_offset)

    def next_line(self) -> "Position":
        return replace(self, row=self.row + 1, column=0)

    def _add_offset(self, offset: int) -> "Position":
        return replace(self, column=self.column + offset)
