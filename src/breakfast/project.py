import logging
from collections.abc import Iterator
from functools import cached_property
from glob import iglob
from pathlib import Path

from breakfast import source
from breakfast.names import all_occurrences
from breakfast.types import Occurrence, Position, Source

logger = logging.getLogger(__name__)


class Project:
    def __init__(self, root: str, source: Source | None = None) -> None:
        self._root = root
        self._initial_source = source

    @cached_property
    def sources(self) -> tuple[Source]:
        return (
            *((self._initial_source,) if self._initial_source else ()),
            *self.find_sources(),
        )

    def get_occurrences(
        self, position: Position, known_sources: list[Source] | None = None
    ) -> list[Occurrence]:
        return sorted(
            all_occurrences(position, sources=self.sources),
            key=lambda o: o.position,
            reverse=True,
        )

    def find_sources(self) -> tuple[Source, ...]:
        sources = tuple(
            source.Source(path=str(path), project_root=self._root)
            for path in get_module_paths(Path(self._root))
        )
        return sources


def get_module_paths(path: Path) -> Iterator[Path]:
    logger.debug(f"{path=}")
    for filename in iglob(f"{path}/**/*.py", recursive=True):
        logger.debug(f"{filename=}")
        module_path = Path(filename)
        if is_allowed(module_path):
            logger.debug(f"{module_path=}")
            yield module_path


EXCLUDE_PATTERNS = (
    "**/__*/**/*.py",
    "__*/*.py",
    "**/*egg-info/**/*.py",
    "*egg-info/*.py",
)


def is_allowed(path: Path) -> bool:
    for pattern in EXCLUDE_PATTERNS:
        if path.match(pattern):
            return False

    return True
