import os
from collections import defaultdict

from ast import NodeVisitor, parse


class Module:

    def __init__(self, path, module_path):
        self.path = path
        self.module_path = module_path

    def get_imported_modules(self):
        with open(self.path) as source:
            node = parse(source.read())
        finder = ImportFinder()
        finder.visit(node)
        return list(finder.imports.keys())

    def imports(self, module):
        return module in self.get_imported_modules()


def find(root):
    for (dirpath, _, filenames) in os.walk(root):
        if any(f.startswith('.') for f in dirpath.split(os.sep)):
            continue
        if dirpath == root or '__init__.py' not in filenames:
            continue
        module_path = dirpath[len(root) + 1:].replace('/', '.')
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
            name = '' if filename == '__init__.py' else '.' + filename[:-3]
            yield Module(
                path=os.path.join(dirpath, filename),
                module_path=module_path + name)


class ImportFinder(NodeVisitor):

    def __init__(self):
        self.imports = defaultdict(set)

    def visit_ImportFrom(self, node):  # noqa
        self.imports[node.module] |= {a.name for a in node.names}
