from collections.abc import Iterable
from textwrap import dedent

from breakfast import types
from breakfast.names import all_occurrence_positions
from breakfast.source import Source


def make_source(code: str, filename: str | None = None) -> types.Source:
    return Source(
        input_lines=tuple(line for line in dedent(code).split("\n")),
        path=filename or "",
        project_root=".",
    )


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
