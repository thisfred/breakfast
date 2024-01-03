import ast
import logging
import os
import re
import sys
from ast import AST, parse
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from breakfast.position import Position

logger = logging.getLogger(__name__)

WORD = re.compile(r"\w+|\W+")


@dataclass(order=True)
class Source:
    path: str
    project_root: str
    lines: tuple[str, ...] | None = None

    def __hash__(self) -> int:
        return hash(self.path)

    def __post_init__(self) -> None:
        self.changes: dict[  # pylint: disable=attribute-defined-outside-init
            int, str
        ] = {}

    def __repr__(self) -> str:
        return f"Source(path={self.path})"

    @property
    def guaranteed_lines(self) -> tuple[str, ...]:
        if self.lines is None:
            with open(self.path, encoding="utf-8") as source_file:
                self.lines = tuple(line[:-1] for line in source_file.readlines())
        return self.lines

    def position(self, row: int, column: int) -> Position:
        return Position(source=self, row=row, column=column)

    def get_name_at(self, position: Position) -> str:
        match = WORD.search(self.get_string_starting_at(position))
        if not match:
            raise AssertionError("no match found")
        return match.group()

    def get_ast(self) -> AST:
        return parse("\n".join(self.guaranteed_lines))

    def get_changes(self) -> Iterator[tuple[int, str]]:
        yield from sorted(self.changes.items())

    def replace(self, position: Position, old: str, new: str) -> None:
        self.modify_line(start=position, end=position + len(old), new=new)

    def modify_line(self, start: Position, end: Position, new: str) -> None:
        line_number = start.row
        line = self.changes.get(line_number, self.guaranteed_lines[line_number])
        modified_line = line[: start.column] + new + line[end.column :]
        self.changes[line_number] = modified_line

    def find_after(self, name: str, start: Position) -> Position:
        regex = re.compile(f"\\b{name}\\b")
        match = regex.search(self.get_string_starting_at(start))
        while start.row < len(self.guaranteed_lines) and not match:
            match = regex.search(self.get_string_starting_at(start))
            start = start.next_line()
        if not match:
            raise AssertionError("no match found")
        return start + match.span()[0]

    def get_string_starting_at(self, position: Position) -> str:
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


class ImportFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: dict[str, set[str]] = defaultdict(set)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa
        if node.module:
            self.imports[node.module] |= {a.asname or a.name for a in node.names}

    def visit_Import(self, node: ast.Import) -> None:  # noqa
        for name in node.names:
            self.imports[name.asname or name.name] = set()
