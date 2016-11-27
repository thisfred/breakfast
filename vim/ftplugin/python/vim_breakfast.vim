" --------------------------------
" Add our plugin to the path
" --------------------------------
python3 import sys
python3 import vim
python3 sys.path.append(vim.eval('expand("<sfile>:h")'))

" --------------------------------
"  Function(s)
" --------------------------------
function! Rename()
python3 << endOfPython

from vim_breakfast import do_rename

def user_input(message):
    """Request user input.

    copied from http://vim.wikia.com/wiki/User_input_from_a_script
    """
    vim.command('call inputsave()')
    vim.command("let user_input = input('" + message + ": ')")
    vim.command('call inputrestore()')
    return vim.eval('user_input')

current = vim.current.buffer
old_name = vim.eval('expand("<cword>")')
new_name = user_input("rename {} to".format(old_name))
(row, column) = vim.current.window.cursor
for i, line in do_rename(buffer_contents=current,
                         old_name=old_name,
                         row=row-1,
                         column=column,
                         new_name=new_name):
    vim.current.buffer[i] = line

endOfPython
endfunction

" --------------------------------
"  Expose our commands to the user
" --------------------------------
command! Rename call Rename()
