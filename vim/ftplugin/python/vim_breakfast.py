from typing import Any, Iterator, List, Tuple

from breakfast.main import Application
from breakfast.source import Source


def do_rename(
    root: str, buffer_contents: List[str], row: int, column: int, new_name: str
) -> Iterator[Tuple[int, str]]:
    source = Source(tuple(buffer_contents), module_name="module")
    application = Application(source=source, root=root)
    application.rename(row=row, column=column, new_name=new_name)
    for i, line in source.get_changes():
        if buffer_contents[i] != line:
            yield (i, line)


def move_to_start_of_word(vim: Any) -> None:  # pragma: nocover
    cursor = vim.current.window.cursor
    vim.command("normal b")
    vim.command("normal w")
    if vim.current.window.cursor != cursor:
        vim.command("normal b")


def user_input(vim: Any, message: str) -> str:  # pragma: nocover
    """Request user input.

    copied from http://vim.wikia.com/wiki/User_input_from_a_script
    """
    vim.command("call inputsave()")
    vim.command("let user_input = input('" + message + ": ')")
    vim.command("call inputrestore()")
    user_input = vim.eval("user_input")
    assert isinstance(user_input, str)
    return user_input
