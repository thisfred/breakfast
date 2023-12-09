from collections.abc import Iterator
from glob import iglob
from pathlib import Path

from breakfast.names import all_occurrence_positions
from breakfast.position import Position
from breakfast.source import Source


class Application:
    def __init__(self, root: str, source: Source | None = None) -> None:
        self._root = root
        self._initial_source = source

    def rename(self, row: int, column: int, new_name: str) -> None:
        if self._initial_source:
            position = Position(self._initial_source, row=row, column=column)
            occurrences = self.get_occurrences(position)
            old_name = self._initial_source.get_name_at(position)
            for occurrence in occurrences:
                occurrence.source.replace(
                    position=occurrence, old=old_name, new=new_name
                )

    def get_occurrences(
        self, position: Position, known_sources: list[Source] | None = None
    ) -> list[Position]:
        return all_occurrence_positions(
            position,
            sources=[self._initial_source] if self._initial_source else [],
            in_reverse_order=True,
        )

    def find_sources(self) -> Iterator[Source]:
        for path in get_module_paths(Path(self._root)):
            yield Source(path=str(path), project_root=self._root)

    def find_importers(self, path: str) -> set[Source]:
        importers = set()
        for source in self.find_sources():
            if path in source.get_imported_modules():
                importers.add(source)

        return importers


def exclude_directory(path: str) -> bool:
    return path.startswith(".") or path.startswith("__") or path.endswith("egg-info")


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


def get_module_paths(path: Path) -> Iterator[Path]:
    for filename in iglob(f"{path}/**/*.py", recursive=True):
        module_path = Path(filename)
        if is_allowed(module_path):
            yield module_path
