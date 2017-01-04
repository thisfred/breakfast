class IllegalPosition(Exception):
    pass


class Position:

    def __init__(self, source, row, column, is_definition=False):
        self.source = source
        self.row = row
        self.column = column
        self.is_definition = is_definition
        if row < 0 or column < 0:
            raise IllegalPosition

    @classmethod
    def from_node(cls, source, node, column_offset=0, row_offset=0,
                  is_definition=False):
        return cls(
            source=source,
            row=(node.lineno - 1) + row_offset,
            column=node.col_offset + column_offset,
            is_definition=is_definition)

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
        return 'Position(row=%s, column=%s%s)' % (
            self.row,
            self.column,
            '' if not self.is_definition else ', is_definition=True')

    def previous(self):
        try:
            return self - 1
        except IllegalPosition:
            return self.source.get_last_column(self.row - 1)

    def next(self):
        position = self + 1
        if position.column <= len(self.source.lines[self.row]):
            return position

        return Position(
            source=self.source,
            row=self.row + 1,
            column=0,
            is_definition=self.is_definition)
