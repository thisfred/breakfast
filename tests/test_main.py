from pathlib import Path

from breakfast.main import Application
from breakfast.source import Source


def test_renames_function_from_lines():
    source = Source(
        (
            "def fun_old():",
            "    return 'result'",
            "result = fun_old()",
        )
    )
    application = Application(source, root=".")

    application.rename(row=0, column=4, new_name="fun_new")

    assert list(source.get_changes()) == [
        (0, "def fun_new():"),
        (2, "result = fun_new()"),
    ]


def test_returns_paths(project_root):
    application = Application(source=Source(("",)), root=project_root)
    found = [
        str(Path(f.path).relative_to(Path(project_root)))
        for f in application.find_modules()
    ]
    assert "tests/data/__init__.py" in found
    assert "tests/data/module1.py" in found
    assert "tests/data/module2.py" in found


def test_returns_module_paths(project_root):
    application = Application(source=Source(("",)), root=project_root)
    found = [f.module_path for f in application.find_modules()]
    assert "tests.data" in found
    assert "tests.data.module1" in found
    assert "tests.data.module2" in found
    assert "tests.data.subpackage" in found


def test_reports_empty_importers(project_root):
    application = Application(source=Source(("",)), root=project_root)
    all_modules = application.find_modules()

    found = [
        module.module_path
        for module in all_modules
        if module.imports("tests.data.module1")
    ]

    assert found == []


def test_reports_importers(project_root):
    application = Application(source=Source(("",)), root=project_root)
    all_modules = application.find_modules()

    found = [
        module.module_path
        for module in all_modules
        if module.imports("tests.data.module2")
    ]

    assert found == ["tests.data.module1"]


def test_all_imports(project_root):
    path = Path("tests") / "data" / "module1.py"
    with path.open("r", encoding="utf-8") as source_file:
        source = Source(
            lines=tuple(line[:-1] for line in source_file.readlines()),
            module_name="tests.data.module2",
            file_name="tests/data/module2.py",
        )
    application = Application(source=source, root=project_root)
    importers = application.find_importers(source.module_name)
    assert {i.module_path for i in importers} == {"tests.data.module1"}
