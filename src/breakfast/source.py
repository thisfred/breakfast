import re
from ast import AST, parse
from collections.abc import Iterator
from dataclasses import dataclass

from breakfast.position import Position

WORD = re.compile(r"\w+|\W+")


@dataclass(order=True)
class Source:
    lines: tuple[str, ...]
    module_name: str = "module"
    filename: str | None = None

    def __hash__(self) -> int:
        return hash(self.filename)

    def __post_init__(self) -> None:
        self.changes: dict[  # pylint: disable=attribute-defined-outside-init
            int, str
        ] = {}

    def __repr__(self) -> str:
        return f"{self.__class__}(lines=[...], module_name={self.module_name!r}, "

    def position(self, row: int, column: int) -> Position:
        return Position(source=self, row=row, column=column)

    def get_name_at(self, position: Position) -> str:
        match = WORD.search(self.get_string_starting_at(position))
        if not match:
            raise AssertionError("no match found")
        return match.group()

    def get_ast(self) -> AST:
        return parse("\n".join(self.lines))

    def get_changes(self) -> Iterator[tuple[int, str]]:
        yield from sorted(self.changes.items())

    def replace(self, position: Position, old: str, new: str) -> None:
        self.modify_line(start=position, end=position + len(old), new=new)

    def modify_line(self, start: Position, end: Position, new: str) -> None:
        line_number = start.row
        line = self.changes.get(line_number, self.lines[line_number])
        modified_line = line[: start.column] + new + line[end.column :]
        self.changes[line_number] = modified_line

    def find_after(self, name: str, start: Position) -> Position:
        regex = re.compile(f"\\b{name}\\b")
        match = regex.search(self.get_string_starting_at(start))
        while start.row <= len(self.lines) and not match:
            start = start.next_line()
            match = regex.search(self.get_string_starting_at(start))
        if not match:
            raise AssertionError("no match found")
        return start + match.span()[0]

    def get_string_starting_at(self, position: Position) -> str:
        return self.lines[position.row][position.column :]
