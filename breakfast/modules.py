import ast

from collections import defaultdict
from typing import TYPE_CHECKING, DefaultDict, List, Optional, Set


if TYPE_CHECKING:
    from breakfast.position import Position
    from breakfast.source import Source  # noqa: F401


class Module:
    def __init__(self, path: str, module_path: str, source: Optional["Source"] = None):
        self.path = path
        self.source = source
        self.module_path = module_path

    def get_imported_modules(self) -> List[str]:
        with open(self.path) as source:
            node = ast.parse(source.read())
        finder = ImportFinder()
        finder.visit(node)
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

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa
        if node.module:
            self.imports[node.module] |= {a.name for a in node.names}
