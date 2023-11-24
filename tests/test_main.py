from pathlib import Path

from breakfast.main import Application
from breakfast.source import Source


def test_renames_function_from_lines():
    source = Source(
        path="wat.py",
        lines=(
            "def fun_old():",
            "    return 'result'",
            "result = fun_old()",
        ),
        project_root="wat",
    )
    application = Application(root=".", source=source)

    application.rename(row=0, column=4, new_name="fun_new")

    assert list(source.get_changes()) == [
        (0, "def fun_new():"),
        (2, "result = fun_new()"),
    ]


def test_returns_paths(project_root):
    application = Application(root=project_root)
    found = [
        str(Path(s.path).relative_to(Path(project_root)))
        for s in application.find_sources()
    ]
    assert "tests/data/__init__.py" in found
    assert "tests/data/module1.py" in found
    assert "tests/data/module2.py" in found


def test_returns_module_names(project_root):
    application = Application(root=project_root)
    found = [s.module_name for s in application.find_sources()]
    assert "tests.data" in found
    assert "tests.data.module1" in found
    assert "tests.data.module2" in found
    assert "tests.data.subpackage" in found


def test_reports_empty_importers(project_root):
    application = Application(root=project_root)
    all_sources = application.find_sources()

    found = [
        source.module_name
        for source in all_sources
        if source.imports("tests.data.module1")
    ]

    assert found == []


def test_reports_importers(project_root):
    application = Application(root=project_root)
    all_sources = application.find_sources()

    found = [
        source.module_name
        for source in all_sources
        if source.imports("tests.data.module2")
    ]

    assert found == ["tests.data.module1"]


def test_all_imports(project_root):
    source = Source(path="tests/data/module2.py", project_root=".")
    application = Application(root=project_root, source=source)
    importers = application.find_importers(source.module_name)
    assert {i.module_name for i in importers} == {"tests.data.module1"}
