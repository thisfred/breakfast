class InvalidPosition(Exception):
    pass


class Position:

    def __init__(self, *, row, column):
        if row < 0 or column < 0:
            raise InvalidPosition
        self.row = row
        self.column = column

    @classmethod
    def from_node(cls, node):
        return cls(row=node.lineno - 1, column=node.col_offset)

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
