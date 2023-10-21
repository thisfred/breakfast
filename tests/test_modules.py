import os

from breakfast.modules import Module

ROOT = os.path.sep.join(os.path.dirname(__file__).split(os.path.sep)[:-1])


def test_reports_empty_importees() -> None:
    root = os.path.sep.join(__file__.split(os.path.sep)[:-2])
    module = Module(
        path=os.path.join(ROOT, "tests", "data", "module2.py"),
        module_path="tests.data.module2",
        project_root=root,
    )
    assert not module.get_imported_modules()


def test_reports_importees() -> None:
    root = os.path.sep.join(__file__.split(os.path.sep)[:-2])
    module = Module(
        path=os.path.join(ROOT, "tests", "data", "module1.py"),
        module_path="tests.data.module1",
        project_root=root,
    )
    assert module.get_imported_modules() == ["os", "tests.data.module2"]


def test_reports_importee_filenames_that_live_in_the_project() -> None:
    root = os.path.sep.join(__file__.split(os.path.sep)[:-2])
    module = Module(
        path=os.path.join(ROOT, "tests", "data", "module1.py"),
        module_path="tests.data.module1",
        project_root=root,
    )
    assert [f.split(os.path.sep)[-1] for f in module.get_imported_files()] == [
        "module2.py",
    ]
