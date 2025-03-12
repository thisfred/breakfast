import ast
from collections.abc import Iterable, Sequence
from pathlib import Path
from textwrap import dedent

from pytest import fixture

from breakfast import types
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
    target: str | types.TextRange,
    code: str | types.Source,
    expected: str,
    occurrence: int = 1,
):
    source = make_source(code) if isinstance(code, str) else code
    selection_range = (
        range_for(target, source, occurrence) if isinstance(target, str) else target
    )
    selection = CodeSelection(selection_range)
    edits = refactoring(selection).edits
    actual = ast.unparse(ast.parse(apply_edits(source=source, edits=edits))).strip()
    expected = ast.unparse(ast.parse(dedent(expected).strip())).strip()

    assert actual == expected


def apply_edits(source: types.Source, edits: Sequence[types.Edit]):
    end = source.position(len(source.lines), 0)
    full_range = TextRange(source.position(0, 0), end)

    new_text = "\n".join(full_range.text_with_substitutions(edits))

    return new_text


def range_for(
    needle: str, source: types.Source, occurrence: int = 1
) -> types.TextRange:
    found = 0
    for row, line in enumerate(source.lines):
        try:
            column = line.text.index(needle)
        except ValueError:
            continue

        found += 1
        if found == occurrence:
            return TextRange(
                source.position(row, column), source.position(row, column + len(needle))
            )

    raise ValueError("not found")
