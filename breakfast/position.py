class IllegalPosition(Exception):
    pass


class Position:

    def __init__(self, source, row, column, is_definition=False, node=None):
        if row < 0 or column < 0:
            raise IllegalPosition
        self.source = source
        self.row = row
        self.column = column
        self.is_definition = is_definition
        self.node = node

    def get_name(self):
        return self.source.get_name_at(self)

    def copy(self, source=None, row=None, column=None, is_definition=None,
             node=None):
        return Position(
            source=source or self.source,
            row=row or self.row,
            column=column or self.column,
            is_definition=is_definition or self.is_definition,
            node=node or self.node)

    def _add_offset(self, offset):
        return Position(
            source=self.source, row=self.row, column=self.column + offset)

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
        return 'Position(row=%s, column=%s%s%s)' % (
            self.row,
            self.column,
            ' (D)' if self.is_definition else '',
            ' %s' % (type(self.node),) if self.node else '')

    def __hash__(self):
        return self.row * 100 + self.column
