from ast import NodeVisitor, parse
from collections import defaultdict


class Name:
    def __init__(self, module, position, value):
        self.module = module
        self.position = position
        self.value = value
        self.occurrences = [self.position]
        self.importable = True
        self.imported = False


class Module:
    def __init__(self, path, module_path, source=None):
        self.path = path
        self.module_path = module_path
        self.source = source

    def get_imported_modules(self):
        with open(self.path) as source:
            node = parse(source.read())
        finder = ImportFinder()
        finder.visit(node)
        return list(finder.imports.keys())

    def imports(self, module):
        return module in self.get_imported_modules()

    def get_name_at(self, position):
        value = self.source.get_name_at(position)
        return Name(self, position, value)


class ImportFinder(NodeVisitor):
    def __init__(self):
        self.imports = defaultdict(set)

    def visit_ImportFrom(self, node):  # noqa
        self.imports[node.module] |= {a.name for a in node.names}
