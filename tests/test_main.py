import os

from breakfast.main import Application
from breakfast.source import Source

ROOT = os.path.sep.join(os.path.dirname(__file__).split(os.path.sep)[:-1])


def test_renames_function_from_lines() -> None:
    source = Source(["def fun_old():", "    return 'result'", "result = fun_old()"])
    application = Application(source, root=".")

    application.rename(row=0, column=4, new_name="fun_new")

    assert list(source.get_changes()) == [
        (0, "def fun_new():"),
        (2, "result = fun_new()"),
    ]


def test_returns_paths() -> None:
    application = Application(source=Source([""]), root=ROOT)
    found = list(
        "/".join(f.path.split(os.path.sep)[-3:]) for f in application.find_modules()
    )
    assert "tests/data/__init__.py" in found
    assert "tests/data/module1.py" in found
    assert "tests/data/module2.py" in found


def test_returns_module_paths() -> None:
    application = Application(source=Source([""]), root=ROOT)
    found = list(f.module_path for f in application.find_modules())
    assert "tests.data" in found
    assert "tests.data.module1" in found
    assert "tests.data.module2" in found
    assert "tests.data.subpackage" in found


def test_reports_empty_importers() -> None:
    application = Application(source=Source([""]), root=ROOT)
    all_modules = application.find_modules()

    found = [
        module.module_path
        for module in all_modules
        if module.imports("tests.data.module1")
    ]

    assert found == []


def test_reports_importers() -> None:
    application = Application(source=Source([""]), root=ROOT)
    all_modules = application.find_modules()

    found = [
        module.module_path
        for module in all_modules
        if module.imports("tests.data.module2")
    ]

    assert found == ["tests.data.module1"]


def ignore_finds_other_modules() -> None:
    with open(os.path.join("tests", "data", "module1.py"), "r") as source_file:
        source = Source(
            lines=[l[:-1] for l in source_file.readlines()],
            module_name="tests.data.module1",
            file_name="tests/data/module1.py",
        )
    application = Application(source=source, root=ROOT)
    assert "tests.data.module2" in [
        m.path for m in application.get_additional_sources()
    ]
