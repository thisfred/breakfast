from ast import AST
from functools import total_ordering
from typing import TYPE_CHECKING, Optional


if TYPE_CHECKING:
    from breakfast.source import Source


class IllegalPosition(Exception):
    pass


@total_ordering
class Position:
    def __init__(
        self, source: "Source", row: int, column: int, node: Optional[AST] = None
    ) -> None:
        if row < 0 or column < 0:
            raise IllegalPosition

        self.source = source
        self.row = row
        self.column = column
        self.node = node

    def __add__(self, column_offset: int) -> "Position":
        return self._add_offset(column_offset)

    def __sub__(self, column_offset: int) -> "Position":
        return self._add_offset(-column_offset)

    def __eq__(self, other: object) -> bool:
        assert isinstance(other, Position)
        return (
            self.source is other.source
            and self.row == other.row
            and self.column == other.column
        )

    def __lt__(self, other: "Position") -> bool:
        return self.source < other.source or (
            self.source is other.source
            and (
                self.row < other.row
                or (self.row == other.row and self.column < other.column)
            )
        )

    def __gt__(self, other: "Position") -> bool:
        return other.source < self.source or (
            other.source is self.source
            and (
                other.row < self.row
                or (other.row == self.row and other.column < self.column)
            )
        )

    def __repr__(self) -> str:
        return "Position(row=%s, column=%s%s)" % (
            self.row,
            self.column,
            ", node=%s" % (repr(self.node),) if self.node else "",
        )

    def copy(
        self,
        source: Optional["Source"] = None,
        row: Optional[int] = None,
        column: Optional[int] = None,
        node: Optional[AST] = None,
    ) -> "Position":
        return Position(
            source=source if source is not None else self.source,
            row=row if row is not None else self.row,
            column=column if column is not None else self.column,
            node=node if node is not None else self.node,
        )

    def _add_offset(self, offset: int) -> "Position":
        return self.copy(column=self.column + offset)
