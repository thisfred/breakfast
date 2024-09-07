import pytest

from breakfast.source import IllegalPositionError, Position, Source


@pytest.fixture
def source() -> Source:
    return Source(path=".", project_root=".")


def test_cannot_create_negative_positions(source: Source) -> None:
    with pytest.raises(IllegalPositionError):
        Position(source=source, row=-1, column=10)


def test_add(source: Source) -> None:
    position = Position(source=source, row=12, column=4)
    new = position + 5
    assert new.column == 9


def test_substract(source: Source) -> None:
    position = Position(source=source, row=12, column=4)
    new = position - 2
    assert new.column == 2


def test_compare(source: Source) -> None:
    assert Position(source=source, row=12, column=4) > Position(
        source=source, row=11, column=4
    )
    assert Position(source=source, row=12, column=4) >= Position(
        source=source, row=11, column=4
    )
    assert Position(source=source, row=12, column=4) != Position(
        source=source, row=11, column=4
    )
    assert Position(source=source, row=12, column=4) != Position(
        source=source, row=11, column=4
    )
    assert Position(source=source, row=12, column=4) == Position(
        source=source, row=12, column=4
    )
    assert Position(source=source, row=12, column=3) <= Position(
        source=source, row=12, column=4
    )
    assert Position(source=source, row=12, column=3) < Position(
        source=source, row=12, column=4
    )
