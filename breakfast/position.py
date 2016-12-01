class InvalidPosition(Exception):
    pass


class Position:

    def __init__(self, *, row, column):
        self.row = row
        self.column = column

    def _add_offset(self, offset: int):
        return Position(row=self.row, column=self.column + offset)

    def __add__(self, column_offset: int):
        return self._add_offset(column_offset)

    def __sub__(self, column_offset: int):
        return self._add_offset(-column_offset)

    def __eq__(self, other) -> bool:
        return self.row == other.row and self.column == other.column

    def __repr__(self):
        return 'Position(row=%s, column=%s)' % (self.row, self.column)
