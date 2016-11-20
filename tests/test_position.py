from breakfast.rename import Position


def test_repr():
    position = Position(row=12, column=4)
    assert "Position(row=12, column=4)" == repr(position)


def test_add():
    position = Position(row=12, column=4)
    new = position + 5
    assert Position(row=12, column=9) == new


def test_substract():
    position = Position(row=12, column=4)
    new = position - 2
    assert Position(row=12, column=2) == new
