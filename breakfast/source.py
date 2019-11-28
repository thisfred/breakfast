import re

from ast import AST, parse
from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Tuple

from breakfast.position import Position


WORD = re.compile(r"\w+|\W+")


@dataclass(order=True)
class Source:

    lines: Tuple[str, ...]
    module_name: str = "module"
    file_name: Optional[str] = None

    def __hash__(self) -> int:
        return hash((self.module_name, self.file_name))

    def __post_init__(self) -> None:
        self.changes: Dict[  # pylint: disable=attribute-defined-outside-init
            int, str
        ] = {}

    def __repr__(self) -> str:
        return (
            f"{self.__class__}(lines=[...], module_name={repr(self.module_name)}, "
            f"file_name={repr(self.file_name)})"
        )

    def position(self, row: int, column: int) -> Position:
        return Position(source=self, row=row, column=column)

    def get_name_at(self, position: Position) -> str:
        match = WORD.search(self.get_string_starting_at(position))
        assert match
        return match.group()

    def get_ast(self) -> AST:
        return parse("\n".join(self.lines))

    def render(self) -> str:
        return "\n".join(self.changes.get(i, line) for i, line in enumerate(self.lines))

    def get_changes(self) -> Iterator[Tuple[int, str]]:
        for change in sorted(self.changes.items()):
            yield change

    def replace(self, position: Position, old: str, new: str) -> None:
        self.modify_line(start=position, end=position + len(old), new=new)

    def modify_line(self, start: Position, end: Position, new: str) -> None:
        line_number = start.row
        line = self.changes.get(line_number, self.lines[line_number])
        modified_line = line[: start.column] + new + line[end.column :]
        self.changes[line_number] = modified_line

    def find_after(self, name: str, start: Position) -> Position:
        regex = re.compile("\\b{}\\b".format(name))
        match = regex.search(self.get_string_starting_at(start))
        while start.row <= len(self.lines) and not match:
            start = start.next_line()
            match = regex.search(self.get_string_starting_at(start))
        assert match
        return start + match.span()[0]

    def get_string_starting_at(self, position: Position) -> str:
        return self.lines[position.row][position.column :]
