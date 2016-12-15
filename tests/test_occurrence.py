import pytest

from breakfast.occurrence import IllegalPosition, Occurrence, Position


def test_position_repr_looks_ok():
    position = Position(row=12, column=4)
    assert "Position(row=12, column=4)" == repr(position)


def test_occurrence_repr_looks_ok():
    occurrence = Occurrence(name='foo', position=Position(row=12, column=4))
    assert (
        "<Occurrence(foo, Position(row=12, column=4), is_definition=False)>" ==
        repr(occurrence))


def test_cannot_create_negative_positions():
    with pytest.raises(IllegalPosition):
        Position(-1, 10)


def test_add():
    position = Position(row=12, column=4)
    new = position + 5
    assert Position(row=12, column=9) == new


def test_substract():
    position = Position(row=12, column=4)
    new = position - 2
    assert Position(row=12, column=2) == new
