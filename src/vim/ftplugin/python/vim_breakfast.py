from collections.abc import Iterator
from typing import Any

from breakfast.main import Application
from breakfast.source import Source


def do_rename(  # pylint: disable=too-many-arguments
    root: str,
    buffer_contents: list[str],
    file_name: str,
    row: int,
    column: int,
    new_name: str,
) -> Iterator[tuple[int, str]]:
    source = Source(tuple(buffer_contents), module_name="module", file_name=file_name)
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
    if not isinstance(user_input, str):
        raise AssertionError(f"user_input must be a string, got `{user_input}`")
    return user_input
