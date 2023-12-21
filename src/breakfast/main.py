import logging
from collections.abc import Iterator
from glob import iglob
from pathlib import Path

from breakfast.names import all_occurrence_positions
from breakfast.position import Position
from breakfast.source import Source

logger = logging.getLogger(__name__)


class Project:
    def __init__(self, root: str, source: Source | None = None) -> None:
        self._root = root
        self._initial_source = source

    def get_occurrences(
        self, position: Position, known_sources: list[Source] | None = None
    ) -> list[Position]:
        return all_occurrence_positions(
            position,
            sources=([self._initial_source] if self._initial_source else [])
            + self.find_sources(),
            in_reverse_order=True,
        )

    def find_sources(self) -> list[Source]:
        sources = [
            Source(path=str(path), project_root=self._root)
            for path in get_module_paths(Path(self._root))
        ]
        return sources

    def find_importers(self, path: str) -> set[Source]:
        importers = set()
        for source in self.find_sources():
            if path in source.get_imported_modules():
                importers.add(source)

        return importers


def get_module_paths(path: Path) -> Iterator[Path]:
    logger.info(f"{path=}")
    for filename in iglob(f"{path}/**/*.py", recursive=True):
        logger.info(f"{filename=}")
        module_path = Path(filename)
        if is_allowed(module_path):
            logger.info(f"{module_path=}")
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
