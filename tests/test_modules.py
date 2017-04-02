import os
from breakfast import modules

ROOT = os.path.sep.join(os.path.dirname(__file__).split(os.path.sep)[:-1])


def test_returns_paths():
    found = list(
        '/'.join(f.path.split(os.path.sep)[-3:])
        for f in modules.find(ROOT))
    assert 'tests/data/__init__.py' in found
    assert 'tests/data/module1.py' in found
    assert 'tests/data/module2.py' in found


def test_returns_module_paths():
    found = list(
        f.module_path
        for f in modules.find(ROOT))
    assert 'tests.data' in found
    assert 'tests.data.module1' in found
    assert 'tests.data.module2' in found
    assert 'tests.data.subpackage' in found


def test_reports_empty_importees():
    module = modules.Module(
        path=os.path.join(ROOT, 'tests', 'data', 'module2.py'),
        module_path='data.module2')
    assert module.get_imported_modules() == []


def test_reports_importees():
    module = modules.Module(
        path=os.path.join(ROOT, 'tests', 'data', 'module1.py'),
        module_path='data.module1')
    assert module.get_imported_modules() == ['tests.data.module2']


def test_reports_empty_importers():
    all_modules = modules.find(ROOT)

    found = [
        module.module_path for module in all_modules
        if module.imports('tests.data.module1')]

    assert found == []


def test_reports_importers():
    all_modules = modules.find(ROOT)

    found = [
        module.module_path for module in all_modules
        if module.imports('tests.data.module2')]

    assert found == ['tests.data.module1']
