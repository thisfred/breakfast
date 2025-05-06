import ast
import re
from collections.abc import Iterable
from pathlib import Path
from textwrap import dedent

from pytest import fixture

from breakfast import types
from breakfast.code_generation import unparse
from breakfast.names import all_occurrence_positions, all_occurrences
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


def assert_ast_equals(code: str, other_code: str) -> None:
    actual = unparse(ast.parse(code))
    expected = unparse(ast.parse(other_code))
    assert actual == expected


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
    editor = refactoring.from_selection(selection)
    assert editor
    edits = editor.edits
    actual = apply_edits(source=source, edits=edits)
    expected = dedent(expected).strip()
    assert_ast_equals(actual, expected)


def assert_renames_to(
    *,
    target: str,
    new: str,
    code: str | types.Source,
    expected: str,
    occurrence: int = 1,
    all_occurrences=all_occurrences,
):
    source = make_source(code) if isinstance(code, str) else code
    selection_range = range_for(target, source, occurrence)
    position = selection_range.start
    occurrences = all_occurrences(
        position, sources=[source], in_reverse_order=True
    )
    edits = [
        types.Edit(
            text_range=TextRange(
                start=o.position, end=o.position + len(target)
            ),
            text=new,
        )
        for o in occurrences
    ]
    actual = apply_edits(source=source, edits=edits)
    expected = dedent(expected).strip()
    assert_ast_equals(actual, expected)


def apply_edits(source: types.Source, edits: Iterable[types.Edit]):
    end = source.position(len(source.lines), 0)
    full_range = TextRange(source.position(0, 0), end)

    edit_list = list(edits)

    new_text = "\n".join(full_range.text_with_substitutions(edit_list))

    print(edit_list)
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
        found_range = TextRange(first_range.start, last_range.end)
        print(f"{found_range=}")
        return found_range

    raise ValueError("not found")  # pragma: nocover
