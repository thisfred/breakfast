from breakfast.rename import rename
from breakfast.source import Source
from breakfast.position import Position


def do_rename(buffer_contents, row, column, new_name):
    module_name = 'module'
    source = Source(buffer_contents)
    sources = rename(
        sources={module_name: source},
        position=Position(source, row=row, column=column),
        new_name=new_name)
    for i, line in sources[module_name].get_changes():
        if buffer_contents[i] != line:
            yield (i, line)


def user_input(vim, message):  # pragma: nocover
    """Request user input.

    copied from http://vim.wikia.com/wiki/User_input_from_a_script
    """
    vim.command('call inputsave()')
    vim.command("let user_input = input('" + message + ": ')")
    vim.command('call inputrestore()')
    return vim.eval('user_input')
