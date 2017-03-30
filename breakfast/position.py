class IllegalPosition(Exception):
    pass


class Position:

    def __init__(self, source, row, column, node=None):
        if row < 0 or column < 0:
            raise IllegalPosition
        self.source = source
        self.row = row
        self.column = column
        self.node = node

    def get_name(self):
        return self.source.get_name_at(self)

    def copy(self, source=None, row=None, column=None, node=None):
        return Position(
            source=source if source is not None else self.source,
            row=row if row is not None else self.row,
            column=column if column is not None else self.column,
            node=node if node is not None else self.node)

    def _add_offset(self, offset):
        return self.copy(column=self.column + offset)

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
            ' %s' % (type(self.node),) if self.node else '')

    def __hash__(self):
        return self.row * 100 + self.column
