from breakfast.position import Position
from breakfast.rename import Rename


def do_rename(buffer_contents, old_name, row, column, new_name):
    module_name = 'module'
    refactoring = Rename(files={module_name: buffer_contents})
    refactoring.initialize(
        module=module_name,
        position=Position(row=row, column=column),
        old_name=old_name,
        new_name=new_name)
    refactoring.apply()
    for i, line in refactoring.get_changes(module_name):
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
