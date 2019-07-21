import os

from typing import TYPE_CHECKING, Iterator, List

from breakfast.modules import Module
from breakfast.names import Names
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
        visitor = Names(self._initial_source)
        visitor.visit_source(self._initial_source)
        for module in self.get_additional_sources():
            if module.source:
                visitor.visit_source(module.source)

        for occurrence in reversed(visitor.get_occurrences(old_name, position)):
            occurrence.source.replace(position=occurrence, old=old_name, new=new_name)

    @staticmethod
    def get_additional_sources() -> List[Module]:
        return []

    def find_modules(self) -> Iterator[Module]:
        for (dirpath, _, filenames) in os.walk(self._root):
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
