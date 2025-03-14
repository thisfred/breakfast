# Inspired by Armin Ronacher's codegen.py, but shares no code with it.

import ast

from pytest import mark

from breakfast.code_generation import to_source
from breakfast.source import Source
from tests.conftest import make_source


@mark.parametrize("code", ('f"{{{2}}}"', "from .foo import bar"))
def test_roundtrip_string_should_result_in_same_ast(code):
    source = make_source(code)
    new_source = "".join(to_source(source.ast, 0))
    assert ast.unparse(source.ast) == ast.unparse(ast.parse(new_source))


@mark.parametrize(
    "filename",
    (
        "src/breakfast/__init__.py",
        "src/breakfast/breakfast_lsp/__init__.py",
        "src/breakfast/breakfast_lsp/__main__.py",
        "src/breakfast/breakfast_lsp/server.py",
        "src/breakfast/code_generation.py",
        "src/breakfast/names.py",
        "src/breakfast/project.py",
        "src/breakfast/refactoring.py",
        "src/breakfast/scope_graph/__init__.py",
        "src/breakfast/scope_graph/scope_graph.py",
        "src/breakfast/scope_graph/visualization.py",
        "src/breakfast/search.py",
        "src/breakfast/source.py",
        "src/breakfast/types.py",
        "src/breakfast/visitor.py",
        "tests/test_code_generation.py",
    ),
)
def test_roundtrip_file_should_result_in_same_ast(filename):
    source = Source(path=filename, project_root=".")
    new_source = "".join(to_source(source.ast, 0))
    assert ast.unparse(source.ast) == ast.unparse(ast.parse(new_source))
