import os
import pkgutil
from collections.abc import Iterator
from typing import TYPE_CHECKING

from breakfast.modules import Module
from breakfast.names import all_occurrence_positions
from breakfast.position import Position

if TYPE_CHECKING:
    from breakfast.source import Source


class Application:
    def __init__(self, source: "Source", root: str) -> None:
        self._initial_source = source
        self._root = root

    def rename(self, row: int, column: int, new_name: str) -> None:
        position = Position(self._initial_source, row=row, column=column)
        old_name = self._initial_source.get_name_at(position)

        occurrences = all_occurrence_positions(
            position,
            sources=[
                source
                for module in [
                    Module(
                        path="",
                        module_path="",
                        project_root="",
                        source=self._initial_source,
                    ),
                    *self.get_additional_sources(),
                ]
                if (source := module.source)
            ],
        )

        for occurrence in reversed(occurrences):
            occurrence.source.replace(position=occurrence, old=old_name, new=new_name)

    @staticmethod
    def get_additional_sources() -> list[Module]:
        return []

    def find_modules(self) -> Iterator[Module]:
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
                        yield Module(path=filename, module_path=m.name, project_root="")

    def find_importers(self, path: str) -> set[Module]:
        importers = set()
        for module in self.find_modules():
            if path in module.get_imported_modules():
                importers.add(module)

        return importers
