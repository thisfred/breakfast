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

from vim_breakfast import do_rename, user_input, move_to_start_of_word

current = vim.current.buffer
vim.command('w')
move_to_start_of_word(vim)

old_name = vim.eval('expand("<cword>")')
new_name = user_input(vim, "rename {} to".format(old_name))
(row, column) = vim.current.window.cursor
for i, line in do_rename(buffer_contents=current,
                         row=row-1,
                         column=column,
                         new_name=new_name):
    vim.current.buffer[i] = line
vim.command('w')

endOfPython
endfunction

" --------------------------------
"  Expose our commands to the user
" --------------------------------
command! Rename call Rename()
