from __future__ import annotations

import ast
import logging
import os
import re
import sys
from ast import AST, parse
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import InitVar, dataclass, replace
from functools import cached_property
from typing import Protocol, TypeGuard

from breakfast import types
from breakfast.configuration import configuration
from breakfast.search import (
    find_names,
    find_statements,
    get_nodes,
    nodes_in_range,
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


@dataclass(order=True, frozen=True, kw_only=True)
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
    def start(self) -> types.Position:
        return self

    @property
    def end(self) -> types.Position:
        return self

    @property
    def start_of_line(self) -> types.Position:
        return replace(self, column=0)

    @property
    def line(self) -> types.Line:
        return self.source.lines[self.row]

    @property
    def indentation(self) -> str:
        text = self.source.lines[self.row].text
        if not (indentation_match := INDENTATION.match(text)):
            return ""

        if not (groups := indentation_match.groups()):
            return ""

        return groups[0]

    @property
    def level(self) -> int:
        indentation = self.indentation
        return (
            len(indentation) // configuration["code_generation"]["indentation"]
        )

    @property
    def as_range(self) -> types.TextRange:
        return TextRange(start=self, end=self)

    def to(self, end: types.Position) -> types.TextRange:
        return TextRange(start=self, end=end)

    def through(self, end: types.Position) -> types.TextRange:
        return TextRange(start=self, end=end + 1)

    def _add_offset(self, offset: int) -> types.Position:
        return replace(self, column=self.column + offset)

    def insert(self, text: str) -> types.Edit:
        return types.Edit(text_range=TextRange(start=self, end=self), text=text)

    def __contains__(self, other: types.Ranged) -> bool:
        return types.contains(self, other)

    def __and__(self, other: types.Ranged) -> types.Ranged:
        if self in other:
            return self

        return EmptyRange(source=self.source)


@dataclass(kw_only=True)
class EmptyRange:
    source: types.Source

    @property
    def start(self) -> types.Position:
        return self.source.position(0, 0)

    @property
    def end(self) -> types.Position:
        return self.source.position(0, 0)

    def __contains__(self, other: types.Ranged) -> bool:
        return False

    def __and__(self, other: types.Ranged) -> types.Ranged:
        return self


@dataclass(order=True, frozen=True, kw_only=True)
class TextRange:
    start: types.Position
    end: types.Position

    @property
    def source(self) -> types.Source:
        return self.start.source

    def __contains__(self, other: types.Ranged) -> bool:
        return types.contains(self, other)

    @cached_property
    def text(self) -> str:
        if self.start >= self.end:
            return ""
        return self.source.get_text(start=self.start, end=self.end)

    @property
    def stripped(self) -> types.TextRange:
        lines = self.text.split("\n")

        start = self.start
        for line in lines:
            if line.lstrip() == line:
                break
            if line.strip():
                start += len(line) - len(line.lstrip())
                break
            else:
                start = start.line.next.start if start.line.next else start
        end = self.end

        for line in lines[::-1]:
            if line.rstrip() == line:
                break
            if line.strip():
                end -= len(line) - len(line.rstrip())
                break
            else:
                end = end.line.previous.end if end.line.previous else end
        return start.to(end)

    @cached_property
    def names(self) -> Sequence[types.Occurrence]:
        names = []
        for occurrence in find_names(self.source.ast, self.source):
            if occurrence.position < self.start:
                continue
            if occurrence.position > self.end:
                break
            names.append(occurrence)

        return names

    @cached_property
    def definitions(self) -> list[types.Occurrence]:
        return [
            occurrence for occurrence in self.names if occurrence.is_definition
        ]

    @cached_property
    def enclosing_scopes(
        self,
    ) -> Sequence[types.ScopeWithRange]:
        result = [
            n
            for n in self.enclosing_nodes
            if has_node_type(n, ast.FunctionDef)
            or has_node_type(n, ast.AsyncFunctionDef)
            or has_node_type(n, ast.ClassDef)
            or has_node_type(n, ast.Module)
        ]
        return result

    @cached_property
    def enclosing_scope(
        self,
    ) -> types.ScopeWithRange:
        return self.enclosing_scopes[-1]

    def enclosing_nodes_by_type[T: ast.AST](
        self, node_type: type[T]
    ) -> Sequence[types.NodeWithRange[T]]:
        return [n for n in self.enclosing_nodes if has_node_type(n, node_type)]

    @cached_property
    def enclosing_nodes(self) -> Sequence[types.NodeWithRange[ast.AST]]:
        source = self.source
        stripped = self.stripped
        scopes = []
        for node in get_nodes(source.ast):
            if hasattr(node, "end_lineno"):
                if source.node_position(node) > self.end:
                    break
                if (
                    node_range := source.node_range(node)
                ) is not None and stripped in node_range:
                    scopes.append(
                        types.NodeWithRange(node=node, range=node_range)
                    )
            elif isinstance(node, ast.Module):
                scopes.append(
                    types.NodeWithRange(
                        node=node,
                        range=TextRange(
                            start=source.position(0, 0),
                            end=source.lines[-1].end,
                        ),
                    )
                )

        return scopes

    @cached_property
    def enclosed_nodes(self) -> Sequence[types.NodeWithRange[ast.AST]]:
        return [
            types.NodeWithRange(node=node, range=node_range)
            for node in nodes_in_range(self.source.ast, self)
            if (node_range := self.source.node_range(node)) is not None
        ]

    @property
    def enclosing_call(self) -> types.NodeWithRange[ast.Call] | None:
        calls = self.enclosing_nodes_by_type(ast.Call)
        return calls[-1] if calls else None

    @property
    def enclosing_assignment(self) -> types.NodeWithRange[ast.Assign] | None:
        assignments = self.enclosing_nodes_by_type(ast.Assign)
        return assignments[-1] if assignments else None

    @property
    def enclosing_annotation_assignment(
        self,
    ) -> types.NodeWithRange[ast.AnnAssign] | None:
        assignments = self.enclosing_nodes_by_type(ast.AnnAssign)
        return assignments[-1] if assignments else None

    @cached_property
    def expression(self) -> ast.expr | None:
        if expressions := self.enclosing_nodes_by_type(ast.expr):
            if expressions[-1].range == self:
                return expressions[-1].node

        return None

    @cached_property
    def statements(self) -> Sequence[ast.stmt]:
        if self.end.column == 0 and self.end.line.previous is not None:
            text_range = self.start.to(self.end.line.previous.end)
        else:
            text_range = self

        enclosing_scopes = text_range.enclosing_scopes

        if enclosing_scopes:
            parent: ast.AST = enclosing_scopes[-1].node
        else:
            parent = self.source.ast

        if not isinstance(
            parent,
            ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        ):
            return []

        found = []
        nodes = deque(parent.body)
        while nodes:
            current_node = nodes.popleft()
            node_range = self.source.node_range(current_node)
            if not node_range:
                continue
            if node_range.start > text_range.end:
                break
            if node_range.end and node_range.end < text_range.start:
                continue
            if node_range.start <= text_range.start:
                if (
                    node_range.end
                    and node_range.end >= text_range.end
                    and not (
                        node_range.start == text_range.start
                        and node_range.end == text_range.end
                    )
                ):
                    nodes.extendleft(
                        reversed(
                            list(
                                find_statements(
                                    current_node, recursive_find=False
                                )
                            )
                        )
                    )
                    continue

            found.append(current_node)

        return found

    def text_with_substitutions(
        self, substitutions: Iterable[types.Edit]
    ) -> Sequence[str]:
        row_offset = self.start.row
        text = [
            line.text
            for line in self.source.lines[self.start.row : self.end.row + 1]
        ]

        for substitution in sorted(substitutions, reverse=True):
            if substitution.end < self.start:
                continue
            if substitution.start > self.end:
                break
            row_index = substitution.start.row - row_offset
            rows = substitution.end.row - substitution.start.row
            new_lines = substitution.text.split("\n")
            new_lines[0] = (
                text[row_index][: substitution.start.column] + new_lines[0]
            )
            new_lines[-1] = (
                new_lines[-1]
                + text[row_index + rows][substitution.end.column :]
            )
            text[row_index : row_index + rows + 1] = new_lines
        return text

    def replace(self, new_text: str) -> types.Edit:
        return types.Edit(
            text_range=TextRange(start=self.start, end=self.end), text=new_text
        )

    def __and__(self, other: types.Ranged) -> types.Ranged:
        if (
            self.source != other.source
            or self.end < other.start
            or other.end < self.start
        ):
            return EmptyRange(source=self.source)

        if self in other:
            return self

        if other in self:
            return other

        return TextRange(
            start=max(self.start, other.start),
            end=min(self.end, other.end),
        )


@dataclass(order=True, frozen=True, kw_only=True)
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
    def text_range(self) -> types.TextRange:
        return TextRange(start=self.start, end=self.end)

    def __contains__(self, other: types.Ranged) -> bool:
        return types.contains(self, other)

    def __and__(self, other: types.Ranged) -> types.Ranged:
        return self.text_range & other

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


@dataclass(order=True, kw_only=True)
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

    @property
    def start(self) -> types.Position:
        return Position(source=self, row=0, column=0)

    @property
    def end(self) -> types.Position:
        if not self.text:
            return Position(source=self, row=0, column=0)
        return Position(
            source=self, row=len(self.text) - 1, column=len(self.text[-1]) - 1
        )

    @property
    def source(self) -> types.Source:
        return self

    def __contains__(self, other: types.Ranged) -> bool:
        return other.source == self

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
        return tuple(Line(source=self, row=i) for i in range(len(self.text)))

    @cached_property
    def ast(self) -> AST:
        return parse("\n".join(self.text))

    def position(self, row: int, column: int) -> types.Position:
        return Position(source=self, row=row, column=column)

    def get_name_at(self, position: types.Position) -> str | None:
        match = WORD.search(self.get_string_starting_at(position))
        if not match:
            return None
        return match.group()

    def get_text(self, *, start: types.Position, end: types.Position) -> str:
        if start.source != end.source or end <= start:
            raise ValueError(f"Could not get text from {start=} to {end=}")
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
            new_start = start.line.next.start if start.line.next else start
            if new_start == start:
                break
            start = new_start

        if not match:
            raise AssertionError("no match found")
        return start + match.span()[0]

    def get_string_starting_at(self, position: types.Position) -> str:
        return self.text[position.row][position.column :]

    @property
    def module_name(self) -> tuple[str, ...]:
        path = self.path

        prefixes = [p for p in sys.path if self.path.startswith(p)]
        if prefixes:
            prefix = max(prefixes)
            if prefix:
                path = path[len(prefix) :]

        if path.startswith(os.path.sep):
            path = path[1:]

        dot_py = ".py"
        if path.endswith(dot_py):
            path = path[: -len(dot_py)]

        __init__ = "/__init__"
        if path.endswith(__init__):
            path = path[: -len(__init__)]

        return tuple(path.split(os.path.sep))

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
        return TextRange(start=start, end=end)
