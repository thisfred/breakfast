import logging
import os
import re
import sys
from ast import AST, parse
from collections.abc import Iterator
from dataclasses import InitVar, dataclass, replace

from breakfast import types

logger = logging.getLogger(__name__)

WORD = re.compile(r"\w+|\W+")


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

    @property
    def start_of_line(self) -> types.Position:
        return replace(self, column=0)

    @property
    def next_line(self) -> types.Position:
        return replace(self, row=self.row + 1, column=0)

    def text_through(self, end: types.Position) -> str:
        assert self.source == end.source  # noqa: S101
        assert end > self  # noqa: S101
        return self.source.get_text(start=self, end=end)

    def text_until(self, end: types.Position) -> str:
        assert self.source == end.source  # noqa: S101
        assert end > self  # noqa: S101
        return self.source.get_text(start=self, end=end - 1)

    def _add_offset(self, offset: int) -> types.Position:
        return replace(self, column=self.column + offset)


@dataclass(order=True, frozen=True)
class Line:
    source: types.Source
    row: int

    def text_through(self, last: types.Line) -> str:
        return "\n".join(line.text for line in self.source.lines[self.row : last.row])

    @property
    def text(self) -> str:
        return self.source.guaranteed_lines[self.row]

    @property
    def start(self) -> types.Position:
        return self.source.position(self.row, 0)

    @property
    def end(self) -> types.Position:
        return self.source.position(self.row, len(self.text) - 1)


@dataclass(order=True)
class Source:
    path: str
    project_root: str
    input_lines: InitVar[tuple[str, ...] | None] = None

    def __hash__(self) -> int:
        return hash(self.path)

    def __post_init__(self, input_lines: tuple[str, ...] | None) -> None:
        self._lines = input_lines
        self.changes: dict[int, str] = {}

    def __repr__(self) -> str:
        return f"Source(path={self.path})"

    @property
    def guaranteed_lines(self) -> tuple[str, ...]:
        if self._lines is None:
            with open(self.path, encoding="utf-8") as source_file:
                self._lines = tuple(line[:-1] for line in source_file.readlines())
        return self._lines

    @property
    def lines(self) -> tuple[types.Line, ...]:
        return tuple(Line(self, i) for i in range(len(self.guaranteed_lines)))

    def position(self, row: int, column: int) -> types.Position:
        return Position(source=self, row=row, column=column)

    def get_name_at(self, position: types.Position) -> str:
        match = WORD.search(self.get_string_starting_at(position))
        if not match:
            raise AssertionError("no match found")
        return match.group()

    def get_ast(self) -> AST:
        return parse("\n".join(self.guaranteed_lines))

    def get_changes(self) -> Iterator[tuple[int, str]]:
        yield from sorted(self.changes.items())

    def get_text(self, *, start: types.Position, end: types.Position) -> str:
        assert start.source == end.source  # noqa: S101
        assert end > start  # noqa: S101
        lines = []
        for i, line in enumerate(self.guaranteed_lines[start.row :]):
            current_row = start.row + i
            if current_row <= end.row:
                offset = start.column if current_row == start.row else 0
                cutoff = end.column + 1 if current_row == end.row else None
                lines.append(line[offset:cutoff])
                continue
            break
        return "\n".join(lines)

    def replace(self, position: types.Position, old: str, new: str) -> None:
        self.modify_line(start=position, end=position + len(old), new=new)

    def modify_line(self, start: types.Position, end: types.Position, new: str) -> None:
        line_number = start.row
        line = self.changes.get(line_number, self.guaranteed_lines[line_number])
        modified_line = line[: start.column] + new + line[end.column :]
        self.changes[line_number] = modified_line

    def find_after(self, name: str, start: types.Position) -> types.Position:
        regex = re.compile(f"\\b{name}\\b")
        match = regex.search(self.get_string_starting_at(start))
        while start.row < len(self.guaranteed_lines) and not match:
            match = regex.search(self.get_string_starting_at(start))
            start = start.next_line
        if not match:
            raise AssertionError("no match found")
        return start + match.span()[0]

    def get_string_starting_at(self, position: types.Position) -> str:
        return self.guaranteed_lines[position.row][position.column :]

    @property
    def module_name(self) -> str:
        path = self.path

        prefixes = [p for p in sys.path if self.path.startswith(p)]
        if prefixes:
            prefix = max(prefixes)
            if prefix:
                path = path[len(prefix) :]

        if path.startswith(os.path.sep):
            path = path[1:]

        # Remove .py
        dot_py = ".py"
        if path.endswith(dot_py):
            path = path[: -len(dot_py)]

        __init__ = "/__init__"
        if path.endswith(__init__):
            path = path[: -len(__init__)]

        path = path.replace(os.path.sep, ".")

        return path

    def node_position(self, node: AST) -> types.Position:
        """
        Return the start position of the node in unicode characters.

        (Note that ast.AST's col_offset is in *bytes*)
        """
        row = node.lineno - 1
        line = self.guaranteed_lines[row]
        if line.isascii():
            column_offset = node.col_offset
        else:
            byte_prefix = line.encode("utf-8")[: node.col_offset]
            column_offset = len(byte_prefix.decode("utf-8"))

        return self.position(row=row, column=column_offset)


class SubSource:
    """Source that parses a single type annotation string.

    `ast.parse()` does not parse type annotations specified as strings. This class
    exists to be able to handle them: we explicitly parse the constant value and proxy
    through to the actual source to get the real positions when needed.

    """

    def __init__(
        self, source: types.Source, start_position: types.Position, code: str
    ) -> None:
        self.parent_source = source
        self.parent_start_position = start_position + 1
        self.code = code

    def __hash__(self) -> int:
        return hash((self.parent_source.path, self.parent_start_position))

    @property
    def module_name(self) -> str:
        return self.parent_source.module_name

    @property
    def path(self) -> str:
        return self.parent_source.path

    def get_string_starting_at(self, position: types.Position) -> str:
        assert (  # noqa: S101
            position.row == 0
        ), "Multiline string type annotations are not supported"
        return self.parent_source.get_string_starting_at(
            self.parent_start_position + position.column
        )

    @property
    def guaranteed_lines(self) -> tuple[str, ...]:
        return tuple(self.code)

    @property
    def lines(self) -> tuple[types.Line, ...]:
        return self.parent_source.lines

    def position(self, row: int, column: int) -> types.Position:
        assert (  # noqa: S101
            row == 0
        ), "Multiline string type annotations are not supported"
        return self.parent_start_position + column

    def find_after(self, name: str, position: types.Position) -> types.Position:
        assert (  # noqa: S101
            position.row == 0
        ), "Multiline string type annotations are not supported"
        return self.parent_source.find_after(
            name, self.parent_start_position + position.column
        )

    def get_ast(self) -> AST:
        return self.parent_source.get_ast()

    def get_text(self, *, start: types.Position, end: types.Position) -> str:
        return self.parent_source.get_text(start=start, end=end)

    def node_position(self, node: AST) -> types.Position:
        row = node.lineno - 1
        assert (  # noqa: S101
            row == 0
        ), "Multiline string type annotations are not supported"
        line = self.guaranteed_lines[row]
        if line.isascii():
            column_offset = node.col_offset
        else:
            byte_prefix = line.encode("utf-8")[: node.col_offset]
            column_offset = len(byte_prefix.decode("utf-8"))

        return self.parent_start_position + column_offset
