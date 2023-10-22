from pathlib import Path

from pytest import fixture


@fixture
def project_root():
    return str(Path(__file__).parent.parent.resolve())
