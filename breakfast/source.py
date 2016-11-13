from typing import Any, Callable, List, Tuple  # noqa
from itertools import takewhile
from breakfast.position import InvalidPosition, Position

FORWARD = 1
BACKWARD = -1


class Source:

    def __init__(self, text: str) -> None:
        self.lines = text.split('\n')

    def render(self):
        return '\n'.join(self.lines)

    def replace(self, *, position: Position, old: str, new: str):
        start = self.get_start(name=old, before=position)
        end = start + len(old)
        line = self.lines[start.row]
        self.lines[start.row] = line[:start.column] + new + line[end.column:]

    def get_name_and_position(self, position: Position) -> Tuple[str, Position]:
        try:
            start = self.get_start_of_name(position)
        except InvalidPosition:
            start = position
        return (
            "".join(
                takewhile(
                    lambda c: valid_name_character(c),
                    [char for char in self.get_string_starting_at(start)])),
            start)

    def get_start(self, *, name: str, before: Position) -> Position:
        while not self.get_string_starting_at(before).startswith(name):
            before = self.get_previous_position(before)
        return before

    def get_string_starting_at(self, position: Position) -> str:
        return self.lines[position.row][position.column:]

    def invalid_previous(self, position: Position) -> bool:
        return not(
            valid_name_character(
                self.get_string_starting_at(
                    self.get_previous_position(position))[0]))

    def walk_until(self,
                   position: Position,
                   direction: int,
                   condition: Callable[[Position], bool]) -> Position:
        while not(condition(position)):
            position = self.walk(position, direction)
        return position

    def walk(self, position: Position, direction: int=FORWARD) -> Position:
        return position + direction

    def get_start_of_name(self, position: Position) -> Position:
        return self.walk_until(
            position=position,
            direction=BACKWARD,
            condition=self.invalid_previous)

    def get_previous_position(self, position: Position) -> Position:
        if position.column == 0:
            new_row = position.row - 1
            position = Position(
                row=new_row, column=len(self.lines[new_row]) - 1)
        else:
            position = Position(row=position.row, column=position.column - 1)
        return position


def valid_name_character(char: str) -> bool:
    return char == '_' or char.isalnum()
