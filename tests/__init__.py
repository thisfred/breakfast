from collections.abc import Iterable

from breakfast import types
from breakfast.names import all_occurrence_positions
from breakfast.source import Source


def dedent(code: str) -> str:
    lines = code.split("\n")
    indentation = len(lines[-1])
    return "\n".join(line[indentation:] for line in lines)


def make_source(code: str, filename: str | None = None) -> types.Source:
    return Source(
        lines=tuple(line for line in dedent(code).split("\n")),
        path=filename or "",
        project_root=".",
    )


def all_occurrence_position_tuples(
    position: types.Position,
    *,
    sources: Iterable[types.Source] | None = None,
    in_reverse_order: bool = False,
    debug: bool = False,
) -> list[tuple[int, int]]:
    return [
        (p.row, p.column)
        for p in all_occurrence_positions(
            position, sources=sources, in_reverse_order=in_reverse_order, debug=debug
        )
    ]
