import ast
import logging
import os
import re
import sys
from ast import AST, parse
from collections.abc import Iterable, Sequence
from dataclasses import InitVar, dataclass, replace
from functools import cached_property
from typing import Protocol, TypeGuard

from breakfast import types
from breakfast.search import (
    find_arguments_passed_in_range,
    find_names,
    get_nodes,
)

logger = logging.getLogger(__name__)

WORD = re.compile(r"\w+|\W+")
INDENTATION = re.compile(r"^(\s+)")


class IllegalPositionError(Exception):
    pass


class PositionalNode(Protocol):
    lineno: int
    col_offset: int
    end_col_offset: int
    end_lineno: int


def has_node_type[S: ast.AST, T: ast.AST](
    nwr: types.NodeWithRange[S], node_type: type[T]
) -> TypeGuard[types.NodeWithRange[T]]:
    return isinstance(nwr.node, node_type)


def has_position(node: AST) -> TypeGuard[PositionalNode]:
    return (
        hasattr(node, "lineno")
        and hasattr(node, "col_offset")
        and hasattr(node, "end_col_offset")
        and hasattr(node, "end_lineno")
    )


@dataclass(order=True, frozen=True)
class Position:
    source: types.Source
    row: int
    column: int

    def __post_init__(self) -> None:
        if self.column < 0:
            raise IllegalPositionError(
                f"Illegal value for column: {self.column}."
            )
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

    @cached_property
    def body_for_callable(self) -> types.TextRange | None:
        def node_filter(node: ast.AST) -> bool:
            return (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and self.source.node_position(node).row == self.row
            )

        found = next(get_nodes(self.source.ast, node_filter), None)

        if not isinstance(found, ast.FunctionDef | ast.AsyncFunctionDef):
            return None

        children = found.body
        start_position = self.source.node_position(children[0])
        end_position = (
            self.source.node_end_position(children[-1])
            or start_position.line.end
        )
        return TextRange(start_position, end_position)

    @property
    def indentation(self) -> str:
        text = self.source.lines[self.row].text
        if not (indentation_match := INDENTATION.match(text)):
            return ""

        if not (groups := indentation_match.groups()):
            return ""

        return groups[0]

    @property
    def as_range(self) -> types.TextRange:
        return TextRange(self, self)

    def to(self, end: types.Position) -> types.TextRange:
        return TextRange(self, end)

    def through(self, end: types.Position) -> types.TextRange:
        return TextRange(self, end + 1)

    def _add_offset(self, offset: int) -> types.Position:
        return replace(self, column=self.column + offset)

    def insert(self, text: str) -> types.Edit:
        return types.Edit(TextRange(start=self, end=self), text=text)


@dataclass(order=True, frozen=True)
class TextRange:
    start: types.Position
    end: types.Position

    @cached_property
    def text(self) -> str:
        return self.start.source.get_text(start=self.start, end=self.end)

    @property
    def source(self) -> types.Source:
        return self.start.source

    @cached_property
    def names(self) -> Sequence[tuple[str, types.Position, ast.expr_context]]:
        names = []
        for name, position, context in find_names(self.source.ast, self.source):
            if position < self.start:
                continue
            if position > self.end:
                break
            names.append((name, position, context))

        return names

    @cached_property
    def definitions(self) -> list[tuple[str, types.Position]]:
        return [
            (name, position)
            for name, position, ctx in self.names
            if isinstance(ctx, ast.Store)
        ]

    def enclosing_scopes(
        self,
    ) -> Sequence[
        types.NodeWithRange[ast.FunctionDef]
        | types.NodeWithRange[ast.AsyncFunctionDef]
        | types.NodeWithRange[ast.ClassDef]
    ]:
        result = [
            n
            for n in self.enclosing_nodes()
            if has_node_type(n, ast.FunctionDef)
            or has_node_type(n, ast.AsyncFunctionDef)
            or has_node_type(n, ast.ClassDef)
        ]
        return result

    def enclosing_nodes_by_type[T: ast.AST](
        self, node_type: type[T]
    ) -> Sequence[types.NodeWithRange[T]]:
        return [
            n for n in self.enclosing_nodes() if has_node_type(n, node_type)
        ]

    def enclosing_nodes(self) -> Sequence[types.NodeWithRange[ast.AST]]:
        source = self.source
        scopes = []
        for node in get_nodes(source.ast):
            if hasattr(node, "end_lineno"):
                if source.node_position(node) > self.end:
                    break
                if (
                    node_range := source.node_range(node)
                ) and self in node_range:
                    scopes.append(types.NodeWithRange(node, node_range))

        return scopes

    def enclosing_call(self) -> types.NodeWithRange[ast.Call] | None:
        calls = self.enclosing_nodes_by_type(ast.Call)
        return calls[-1] if calls else None

    def enclosing_assignment(self) -> types.NodeWithRange[ast.Assign] | None:
        types = ast.Assign
        assignments = self.enclosing_nodes_by_type(types)
        return assignments[-1] if assignments else None

    @cached_property
    def statements(self) -> Iterable[ast.stmt]:
        if self.end.column == 0 and self.end.line.previous is not None:
            text_range = self.start.to(self.end.line.previous.end)
        else:
            text_range = self
        enclosing_scopes = text_range.enclosing_scopes()
        if enclosing_scopes:
            parent: ast.AST = enclosing_scopes[-1].node
        else:
            parent = self.source.ast

        if not isinstance(
            parent,
            ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        ):
            return

        for node in parent.body:
            if self.source.node_position(node) < text_range.start:
                continue
            if self.source.node_position(node) > text_range.end:
                break
            yield node

    def text_with_substitutions(
        self, substitutions: Sequence[types.Edit]
    ) -> Sequence[str]:
        row_offset = self.start.row
        text = [
            line.text
            for line in self.start.source.lines[
                self.start.row : self.end.row + 1
            ]
        ]

        for substitution in sorted(substitutions, reverse=True):
            if substitution.text_range.end < self.start:
                continue
            if substitution.text_range.start > self.end:
                break
            row_index = substitution.text_range.start.row - row_offset
            rows = (
                substitution.text_range.end.row
                - substitution.text_range.start.row
            )
            new_lines = substitution.text.split("\n")
            new_lines[0] = (
                text[row_index][: substitution.text_range.start.column]
                + new_lines[0]
            )
            new_lines[-1] = (
                new_lines[-1]
                + text[row_index + rows][substitution.text_range.end.column :]
            )
            text[row_index : row_index + rows + 1] = new_lines
        return text

    def replace(self, new_text: str) -> types.Edit:
        return types.Edit(TextRange(self.start, self.end), text=new_text)

    def contains_as_argument(self, name: str) -> bool:
        return any(
            n == name
            for n in find_arguments_passed_in_range(self.start.source.ast, self)
        )

    def __contains__(
        self, position_or_range: types.Position | types.TextRange
    ) -> bool:
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
                self._lines = tuple(
                    line[:-1] for line in source_file.readlines()
                )
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
                cutoff = end.column if current_row == end.row else None
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
        if not has_position(node):
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
        if not has_position(node):
            return None

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
        if not has_position(node):
            return self.position(0, 0)
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
        if not has_position(node):
            return None

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

    def get_name_at(self, position: types.Position) -> str:
        return self.parent_source.get_name_at(position)
