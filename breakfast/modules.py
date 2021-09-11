import ast

from collections import defaultdict
from typing import TYPE_CHECKING, DefaultDict, List, Optional, Set

from breakfast.source import Source


if TYPE_CHECKING:
    from breakfast.position import Position


class Module:
    def __init__(self, path: str, module_path: str, source: Optional["Source"] = None):
        self.path = path
        self.source = source
        self.module_path = module_path

    def get_imported_modules(self) -> List[str]:
        if self.source is None:
            with open(self.path, encoding="utf-8") as source_file:
                self.source = Source(
                    lines=tuple(line[:-1] for line in source_file.readlines()),
                    module_name=self.module_path,
                    file_name=self.path,
                )

        finder = ImportFinder()
        finder.visit(self.source.get_ast())
        return list(finder.imports.keys())

    def imports(self, module_path: str) -> bool:
        return module_path in self.get_imported_modules()

    def get_name_at(self, position: "Position") -> Optional["Name"]:
        if not self.source:
            return None

        value = self.source.get_name_at(position)
        return Name(self, position, value)


class Name:
    def __init__(self, module: Module, position: "Position", value: str) -> None:
        self.module = module
        self.position = position
        self.value = value
        self.occurrences = [self.position]
        self.importable = True
        self.imported = False


class ImportFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: DefaultDict[str, Set[str]] = defaultdict(set)

    def visit_ImportFrom(  # pylint: disable=invalid-name
        self, node: ast.ImportFrom
    ) -> None:
        if node.module:
            self.imports[node.module] |= {a.asname or a.name for a in node.names}

    def visit_Import(self, node: ast.Import) -> None:  # pylint: disable=invalid-name
        for name in node.names:
            self.imports[name.asname or name.name] = set()
