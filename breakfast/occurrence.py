class IllegalPosition(Exception):
    pass


class Occurrence:

    def __init__(self, name, position, is_definition=False):
        self.name = name
        self.position = position
        self.is_definition = is_definition

    def __repr__(self):
        return ("<Occurrence({}, {}, is_definition={})>".format(
                self.name, self.position, self.is_definition))


class Position:

    def __init__(self, row, column):
        if row < 0 or column < 0:
            raise IllegalPosition
        self.row = row
        self.column = column

    def _add_offset(self, offset):
        return Position(row=self.row, column=self.column + offset)

    def __add__(self, column_offset):
        return self._add_offset(column_offset)

    def __sub__(self, column_offset):
        return self._add_offset(-column_offset)

    def __eq__(self, other):
        return self.row == other.row and self.column == other.column

    def __repr__(self):
        return 'Position(row=%s, column=%s)' % (self.row, self.column)
