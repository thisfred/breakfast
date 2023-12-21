from pathlib import Path

from breakfast.main import Project, get_module_paths, is_allowed
from breakfast.source import Source


def test_returns_paths(project_root):
    application = Project(root=project_root)
    found = [
        str(Path(s.path).relative_to(Path(project_root)))
        for s in application.find_sources()
    ]
    assert "tests/data/__init__.py" in found
    assert "tests/data/module1.py" in found
    assert "tests/data/module2.py" in found


def test_returns_module_names(project_root):
    application = Project(root=project_root)
    found = [s.module_name for s in application.find_sources()]
    assert "tests.data" in found
    assert "tests.data.module1" in found
    assert "tests.data.module2" in found
    assert "tests.data.subpackage" in found


def test_reports_empty_importers(project_root):
    application = Project(root=project_root)
    all_sources = application.find_sources()

    found = [
        source.module_name
        for source in all_sources
        if source.imports("tests.data.module1")
    ]

    assert found == []


def test_reports_importers(project_root):
    application = Project(root=project_root)
    all_sources = application.find_sources()

    found = [
        source.module_name
        for source in all_sources
        if source.imports("tests.data.module2")
    ]

    assert found == ["tests.data.module1"]


def test_all_imports(project_root):
    source = Source(path="tests/data/module2.py", project_root=".")
    application = Project(root=project_root, source=source)
    importers = application.find_importers(source.module_name)
    assert {i.module_name for i in importers} == {"tests.data.module1"}


def test_dunder_directory_names_are_not_allowed():
    assert not is_allowed(Path("__pycache__/foo.py"))
    assert not is_allowed(Path("dir/__pycache__/foo.py"))
    assert not is_allowed(Path("dir1/__pycache__/dir2/foo.py"))


def test_egg_info_directory_names_are_not_allowed():
    assert not is_allowed(Path("foo.egg-info/foo.py"))
    assert not is_allowed(Path("dir/foo.egg-info/foo.py"))
    assert not is_allowed(Path("dir1/foo.egg-info/dir2/foo.py"))


def test_get_module_paths_should_return_python_files(project_root):
    assert "module1.py" in [
        p.name for p in get_module_paths(Path(project_root) / "tests" / "data")
    ]


def test_get_module_paths_should_return_nested_python_files(project_root):
    assert "subpackage" in {
        p.parent.name for p in get_module_paths(Path(project_root) / "tests" / "data")
    }


def test_get_module_paths_should_not_return_text_files(project_root):
    assert "txt" not in {
        p.suffix for p in get_module_paths(Path(project_root) / "tests" / "data")
    }


def test_get_module_paths_should_not_return_python_files_in_dotted_directories(
    project_root,
):
    assert ".ignore" not in {
        p.parent.name for p in get_module_paths(Path(project_root) / "tests" / "data")
    }
    assert "wat.py" not in {
        p.name for p in get_module_paths(Path(project_root) / "tests" / "data")
    }
