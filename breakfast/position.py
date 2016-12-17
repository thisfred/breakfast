class IllegalPosition(Exception):
    pass


class Position:

    def __init__(self, row, column, is_definition=False):
        if row < 0 or column < 0:
            raise IllegalPosition
        self.row = row
        self.column = column
        self.is_definition = is_definition

    def _add_offset(self, offset):
        return Position(row=self.row, column=self.column + offset)

    def __add__(self, column_offset):
        return self._add_offset(column_offset)

    def __sub__(self, column_offset):
        return self._add_offset(-column_offset)

    def __eq__(self, other):
        return self.row == other.row and self.column == other.column

    def __lt__(self, other):
        return self.row < other.row or (
            self.row == other.row and self.column < other.column)

    def __repr__(self):
        return 'Position(row=%s, column=%s%s)' % (
            self.row,
            self.column,
            '' if not self.is_definition else ', is_definition=True')
