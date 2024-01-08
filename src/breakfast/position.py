from dataclasses import dataclass, replace

from breakfast import types


class IllegalPositionError(Exception):
    pass


@dataclass(order=True, frozen=True)
class Position:
    source: types.Source
    row: int
    column: int

    def __post_init__(self) -> None:
        if self.column < 0:
            raise IllegalPositionError(f"Illegal value for column: {self.column}.")
        if self.row < 0:
            raise IllegalPositionError(f"Illegal value for row: {self.row}.")

    def __add__(self, column_offset: int, /) -> types.Position:
        return self._add_offset(column_offset)

    def __sub__(self, to_subtract: int, /) -> types.Position:
        if to_subtract > self.column:
            raise IllegalPositionError()

        return self._add_offset(-to_subtract)

    def next_line(self) -> types.Position:
        return replace(self, row=self.row + 1, column=0)

    def _add_offset(self, offset: int) -> types.Position:
        return replace(self, column=self.column + offset)

    def __repr__(self) -> str:
        return (
            f"<Position(row={self.row}, column={self.column}, "
            f"source=Source(path={self.source.path})"
        )

    # def __lt__(self, other: "Position", /) -> bool:
    #     return self.source < other.source or (
    #         self.source == other.source
    #         and (
    #             self.row < other.row
    #             or self.row == other.row
    #             and (self.column < other.column)
    #         )
    #     )
