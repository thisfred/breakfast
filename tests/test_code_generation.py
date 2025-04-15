import ast

from pytest import mark

from breakfast.code_generation import to_source
from breakfast.source import Source
from tests.conftest import make_source


@mark.parametrize(
    "code",
    (
        'f"{{{2}}}"',
        "from .foo import bar",
        "if a:\n    print(a)\nelif b:\n    print(b)\nelse:    print(c)",
        'f = lambda n: f"${n:,.2f}"',
        'match play:\n    case {"type": "tragedy"}:\n        print(play)',
        "node_end and node_end >= text_range.end "
        "and not (node_start == text_range.start "
        "and node_end == text_range.end)",
        "a - (b + c)",
    ),
)
def test_roundtrip_string_should_result_in_same_ast(code):
    source = make_source(code)
    new_source = "".join(to_source(source.ast, 0))
    assert ast.unparse(source.ast) == ast.unparse(ast.parse(new_source))


@mark.parametrize(
    "code",
    (
        "foo() + 2",
        "a: A | B | C = x",
        "a = 2 - 8 + 5 * 10",
        "node_end and node_end >= text_range.end "
        "and not (node_start == text_range.start "
        "and node_end == text_range.end)",
    ),
)
def test_to_source_should_not_add_unnecessary_parentheses(code):
    source = make_source(code)
    new_source = "".join(to_source(source.ast, 0))
    assert new_source.strip() == code


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
        "src/breakfast/rewrites.py",
        "src/breakfast/scope_graph/__init__.py",
        "src/breakfast/scope_graph/scope_graph.py",
        "src/breakfast/scope_graph/visualization.py",
        "src/breakfast/search.py",
        "src/breakfast/source.py",
        "src/breakfast/types.py",
        "src/breakfast/visitor.py",
        "tests/conftest.py",
        "tests/test_code_generation.py",
        "tests/test_main.py",
        "tests/test_names.py",
        "tests/test_position.py",
        "tests/test_refactoring.py",
        "tests/test_scope_graph.py",
        "tests/test_source.py",
    ),
)
def test_roundtrip_file_should_result_in_same_ast(filename):
    source = Source(path=filename, project_root=".")
    new_source = "".join(to_source(source.ast, 0))
    assert ast.unparse(source.ast) == ast.unparse(ast.parse(new_source))
