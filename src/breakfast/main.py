import os
import pkgutil
from collections.abc import Iterator

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

    def get_occurrences(self, position: Position) -> list[Position]:
        return all_occurrence_positions(
            position,
            sources=[self._initial_source] if self._initial_source else [],
            in_reverse_order=True,
        )

    def find_sources(self) -> Iterator[Source]:
        _, directories, _ = next(os.walk(self._root))
        for dirpath in directories:
            # XXX: this is hacky and probably doesn't cover half of the special kinds of
            # directories that aren't actually packages.
            if (
                dirpath.startswith(".")
                or dirpath.startswith("__")
                or dirpath.endswith("egg-info")
            ):
                continue
            root = dirpath.split(os.path.sep)[-1]
            for m in pkgutil.walk_packages(path=[root], prefix=f"{root}."):
                name = m.name
                loader = pkgutil.get_loader(name)
                if loader:
                    try:
                        filename = loader.get_filename()  # type: ignore[attr-defined]
                    except AttributeError:
                        continue
                    if filename.endswith(".py"):
                        yield Source(
                            path=filename, module_name=m.name, project_root=self._root
                        )

    def find_importers(self, path: str) -> set[Source]:
        importers = set()
        for source in self.find_sources():
            if path in source.get_imported_modules():
                importers.add(source)

        return importers
