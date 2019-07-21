import os

from breakfast.modules import Module

ROOT = os.path.sep.join(os.path.dirname(__file__).split(os.path.sep)[:-1])


def test_reports_empty_importees():
    module = Module(
        path=os.path.join(ROOT, "tests", "data", "module2.py"),
        module_path="data.module2",
    )
    assert module.get_imported_modules() == []


def test_reports_importees():
    module = Module(
        path=os.path.join(ROOT, "tests", "data", "module1.py"),
        module_path="data.module1",
    )
    assert module.get_imported_modules() == ["tests.data.module2"]
