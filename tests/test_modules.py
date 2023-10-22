import os

from breakfast.modules import Module
from pytest import fixture

ROOT = os.path.sep.join(os.path.dirname(__file__).split(os.path.sep)[:-1])


@fixture
def project_root():
    return os.path.sep.join(__file__.split(os.path.sep)[:-2])


def test_reports_empty_importees(project_root):
    module = Module(
        path=os.path.join(ROOT, "tests", "data", "module2.py"),
        module_path="tests.data.module2",
        project_root=project_root,
    )
    assert not module.get_imported_modules()


def test_reports_importees(project_root):
    module = Module(
        path=os.path.join(ROOT, "tests", "data", "module1.py"),
        module_path="tests.data.module1",
        project_root=project_root,
    )
    assert module.get_imported_modules() == ["os", "tests.data.module2"]


def test_reports_importee_filenames_that_live_in_the_project(project_root):
    module = Module(
        path=os.path.join(ROOT, "tests", "data", "module1.py"),
        module_path="tests.data.module1",
        project_root=project_root,
    )
    assert [(f.split(os.path.sep)[-1], p) for f, p in module.get_imported_files()] == [
        ("module2.py", "tests.data.module2")
    ]
