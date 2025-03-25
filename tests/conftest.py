import ast
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from textwrap import dedent

from pytest import fixture

from breakfast import types
from breakfast.code_generation import unparse
from breakfast.names import all_occurrence_positions
from breakfast.refactoring import CodeSelection, Refactoring
from breakfast.source import Source, TextRange


def all_occurrence_position_tuples(
    position: types.Position,
    *,
    sources: Iterable[types.Source] | None = None,
    debug: bool = False,
) -> list[tuple[int, int]]:
    return [
        (p.row, p.column)
        for p in all_occurrence_positions(
            position, sources=sources, in_reverse_order=False, debug=debug
        )
    ]


def make_source(code: str, filename: str | None = None) -> types.Source:
    return Source(
        input_lines=tuple(line for line in dedent(code).split("\n")),
        path=filename or "",
        project_root=".",
    )


@fixture
def project_root():
    return str(Path(__file__).parent.parent.resolve())


def assert_refactors_to(
    *,
    refactoring: type[Refactoring],
    target: str | tuple[str, str],
    code: str | types.Source,
    expected: str,
    occurrence: int = 1,
):
    source = make_source(code) if isinstance(code, str) else code
    selection_range = range_for(target, source, occurrence)
    selection = CodeSelection(selection_range)
    edits = refactoring(selection).edits
    actual = unparse(ast.parse(apply_edits(source=source, edits=edits)))
    expected = unparse(ast.parse(dedent(expected).strip()))

    assert actual == expected


def apply_edits(source: types.Source, edits: Sequence[types.Edit]):
    end = source.position(len(source.lines), 0)
    full_range = TextRange(source.position(0, 0), end)

    new_text = "\n".join(full_range.text_with_substitutions(edits))

    print(edits)
    print(new_text)
    return new_text


def range_for(
    target: str | tuple[str, str], source: types.Source, occurrence: int = 1
) -> types.TextRange:
    found = 0
    needles = target if isinstance(target, tuple) else (target,)

    first_range = None
    last_range = None
    for i, needle in enumerate(needles):
        for row, line in enumerate(source.lines):
            if re.escape(needle) == needle:
                pattern = rf"\b{re.escape(needle)}\b"
            else:
                pattern = re.escape(needle)

            for match in re.finditer(pattern, line.text):
                if i == 0:
                    found += 1
                if i > 0 or found == occurrence:
                    last_range = TextRange(
                        source.position(row, match.start()),
                        source.position(row, match.start() + len(needle)),
                    )
                    if first_range is None:
                        first_range = last_range
                    break

    if first_range and last_range:
        return TextRange(first_range.start, last_range.end)

    raise ValueError("not found")
