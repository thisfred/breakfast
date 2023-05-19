import os
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
                    Module(path="", module_path="", source=self._initial_source),
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
        for dirpath, _, filenames in os.walk(self._root):
            if any(f.startswith(".") for f in dirpath.split(os.sep)):
                continue
            if dirpath == self._root or "__init__.py" not in filenames:
                continue
            module_path = dirpath[len(self._root) + 1 :].replace("/", ".")
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                name = "" if filename == "__init__.py" else "." + filename[:-3]
                yield Module(
                    path=os.path.join(dirpath, filename), module_path=module_path + name
                )

    def find_importers(self, path: str) -> set[Module]:
        importers = set()
        for module in self.find_modules():
            if path in module.get_imported_modules():
                importers.add(module)

        return importers
