import logging
import os
import re
import sys
from ast import AST, Module, parse
from dataclasses import InitVar, dataclass, replace
from functools import cached_property

from breakfast import types
from breakfast.search import find_functions, find_scopes

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
    def line(self) -> types.Line:
        return self.source.lines[self.row]

    def through(self, end: types.Position) -> types.TextRange:
        return TextRange(self, end)

    def _add_offset(self, offset: int) -> types.Position:
        return replace(self, column=self.column + offset)


@dataclass(order=True, frozen=True)
class TextRange:
    start: types.Position
    end: types.Position

    @property
    def text(self) -> str:
        return self.start.source.get_text(start=self.start, end=self.end)

    def __contains__(self, position_or_range: types.Position | types.TextRange) -> bool:
        match position_or_range:
            case Position() as position:
                return self.start <= position and self.end >= position
            case TextRange(start, end):
                return self.start <= start and self.end >= end
            case _:
                return False


@dataclass(order=True, frozen=True)
class Line:
    source: types.Source
    row: int

    @property
    def text(self) -> str:
        return self.source.text[self.row]

    @property
    def start(self) -> types.Position:
        return self.source.position(self.row, 0)

    @property
    def end(self) -> types.Position:
        return self.source.position(self.row, max(len(self.text) - 1, 0))

    @property
    def previous(self) -> types.Line | None:
        if self.row == 0:
            return None
        return self.source.lines[self.row - 1]

    @property
    def next(self) -> types.Line | None:
        if self.row >= len(self.source.lines) - 1:
            return None
        return self.source.lines[self.row + 1]


@dataclass(order=True)
class Source:
    path: str
    project_root: str
    input_lines: InitVar[tuple[str, ...] | None] = None

    def __hash__(self) -> int:
        return hash(self.path)

    def __post_init__(self, input_lines: tuple[str, ...] | None) -> None:
        self._lines = input_lines

    def __repr__(self) -> str:
        return f"Source(path={self.path})"

    @cached_property
    def text(self) -> tuple[str, ...]:
        if self._lines is None:
            with open(self.path, encoding="utf-8") as source_file:
                self._lines = tuple(line[:-1] for line in source_file.readlines())
        return self._lines

    @cached_property
    def lines(self) -> tuple[types.Line, ...]:
        return tuple(Line(self, i) for i in range(len(self.text)))

    @cached_property
    def ast(self) -> AST:
        return parse("\n".join(self.text))

    def position(self, row: int, column: int) -> types.Position:
        return Position(source=self, row=row, column=column)

    def get_name_at(self, position: types.Position) -> str:
        match = WORD.search(self.get_string_starting_at(position))
        if not match:
            raise AssertionError("no match found")
        return match.group()

    def get_text(self, *, start: types.Position, end: types.Position) -> str:
        assert start.source == end.source  # noqa: S101
        assert end > start  # noqa: S101
        lines = []
        for i, line in enumerate(self.text[start.row :]):
            current_row = start.row + i
            if current_row <= end.row:
                offset = start.column if current_row == start.row else 0
                cutoff = end.column + 1 if current_row == end.row else None
                lines.append(line[offset:cutoff])
                continue
            break
        return "\n".join(lines)

    def find_after(self, name: str, start: types.Position) -> types.Position:
        regex = re.compile(f"\\b{name}\\b")
        match = regex.search(self.get_string_starting_at(start))
        while start.row < len(self.text) and not match:
            match = regex.search(self.get_string_starting_at(start))
            start = start.line.next.start if start.line.next else start
        if not match:
            raise AssertionError("no match found")
        return start + match.span()[0]

    def get_string_starting_at(self, position: types.Position) -> str:
        return self.text[position.row][position.column :]

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
        if isinstance(node, Module):
            return self.position(0, 0)
        row = node.lineno - 1
        line = self.text[row]
        if line.isascii():
            column_offset = node.col_offset
        else:
            byte_prefix = line.encode("utf-8")[: node.col_offset]
            column_offset = len(byte_prefix.decode("utf-8"))

        return self.position(row=row, column=column_offset)

    def node_end_position(self, node: AST) -> types.Position | None:
        """
        Return the start position of the node in unicode characters.

        (Note that ast.AST's col_offset is in *bytes*)
        """
        if node.end_lineno is None or node.end_col_offset is None:
            return None

        row = node.end_lineno - 1
        line = self.text[row]
        if line.isascii():
            column_offset = node.end_col_offset
        else:
            byte_prefix = line.encode("utf-8")[: node.end_col_offset]
            column_offset = len(byte_prefix.decode("utf-8"))

        return self.position(row=row, column=column_offset)

    def node_range(self, node: AST) -> types.TextRange | None:
        end = self.node_end_position(node)
        if not end:
            return None
        start = self.node_position(node)
        return TextRange(start, end)

    def get_enclosing_function_range(
        self, position: types.Position
    ) -> types.TextRange | None:
        ast = self.ast
        enclosing_ranges = [
            text_range
            for f in find_functions(ast, up_to=position)
            if (text_range := self.node_range(f)) and position in text_range
        ]
        if enclosing_ranges:
            return enclosing_ranges[-1]

        return None

    def get_largest_enclosing_scope_range(
        self, position: types.Position
    ) -> types.TextRange | None:
        ast = self.ast
        return next(
            (
                text_range
                for f in find_scopes(ast, up_to=position)
                if (text_range := self.node_range(f)) and position in text_range
            ),
            None,
        )


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

    @property
    def ast(self) -> AST:
        return self.parent_source.ast

    @property
    def text(self) -> tuple[str, ...]:
        return tuple(self.code)

    @property
    def lines(self) -> tuple[types.Line, ...]:
        return self.parent_source.lines

    def get_string_starting_at(self, position: types.Position) -> str:
        assert (  # noqa: S101
            position.row == 0
        ), "Multiline string type annotations are not supported"
        return self.parent_source.get_string_starting_at(
            self.parent_start_position + position.column
        )

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

    def get_text(self, *, start: types.Position, end: types.Position) -> str:
        return self.parent_source.get_text(start=start, end=end)

    def node_position(self, node: AST) -> types.Position:
        row = node.lineno - 1
        assert (  # noqa: S101
            row == 0
        ), "Multiline string type annotations are not supported"
        line = self.text[row]
        if line.isascii():
            column_offset = node.col_offset
        else:
            byte_prefix = line.encode("utf-8")[: node.col_offset]
            column_offset = len(byte_prefix.decode("utf-8"))

        return self.parent_start_position + column_offset

    def node_end_position(self, node: AST) -> types.Position | None:
        row = node.lineno - 1
        assert (  # noqa: S101
            row == 0
        ), "Multiline string type annotations are not supported"
        line = self.text[row]
        if node.end_col_offset is None:
            return None

        if line.isascii():
            column_offset = node.end_col_offset
        else:
            byte_prefix = line.encode("utf-8")[: node.end_col_offset]
            column_offset = len(byte_prefix.decode("utf-8"))

        return self.parent_start_position + column_offset

    def node_range(self, node: AST) -> TextRange | None:
        end = self.node_end_position(node)
        if not end:
            return None
        start = self.node_position(node)
        return TextRange(start, end)

    def get_enclosing_function_range(
        self, position: types.Position
    ) -> types.TextRange | None:
        return self.parent_source.get_enclosing_function_range(position)

    def get_largest_enclosing_scope_range(
        self, position: types.Position
    ) -> types.TextRange | None:
        return self.parent_source.get_largest_enclosing_scope_range(position)

    def get_name_at(self, position: types.Position) -> str:
        return self.parent_source.get_name_at(position)
