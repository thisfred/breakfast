import re
from ast import AST, parse
from functools import total_ordering
from typing import TYPE_CHECKING, Dict, Iterator, List, Optional, Tuple

if TYPE_CHECKING:
    from breakfast.position import Position


@total_ordering
class Source:

    word = re.compile(r"\w+|\W+")

    def __init__(
        self,
        lines: List[str],
        module_name: str = "module",
        file_name: Optional[str] = None,
    ):
        self.lines = lines
        self.changes: Dict[int, str] = {}
        self.module_name = module_name
        self.file_name = file_name

    def __eq__(self, other: object) -> bool:
        return self is other

    def __lt__(self, other: "Source") -> bool:
        return self.module_name < other.module_name

    def __gt__(self, other: "Source") -> bool:
        return other.module_name < self.module_name

    def get_name_at(self, position: "Position") -> str:
        match = self.word.search(self.get_string_starting_at(position))
        assert match
        return match.group()

    def get_ast(self) -> AST:
        return parse("\n".join(self.lines))

    def render(self) -> str:
        return "\n".join(self.changes.get(i, line) for i, line in enumerate(self.lines))

    def get_changes(self) -> Iterator[Tuple[int, str]]:
        for change in sorted(self.changes.items()):
            yield change

    def replace(self, position: "Position", old: str, new: str) -> None:
        self.modify_line(start=position, end=position + len(old), new=new)

    def modify_line(self, start: "Position", end: "Position", new: str) -> None:
        line_number = start.row
        line = self.changes.get(line_number, self.lines[line_number])
        modified_line = line[: start.column] + new + line[end.column :]
        self.changes[line_number] = modified_line

    def find_after(self, name: str, start: "Position") -> "Position":
        regex = re.compile("\\b{}\\b".format(name))
        match = regex.search(self.get_string_starting_at(start))
        while start.row <= len(self.lines) and not match:
            start = start.copy(row=start.row + 1, column=0)
            match = regex.search(self.get_string_starting_at(start))
        assert match
        return start.copy(column=start.column + match.span()[0])

    def get_string_starting_at(self, position: "Position") -> str:
        return self.lines[position.row][position.column :]
