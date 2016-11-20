from breakfast.position import Position
from breakfast.rename import rename


def do_rename(buffer_contents, old_name, row, column, new_name):
    return rename(
        source="\n".join(buffer_contents),
        cursor=Position(row=row, column=column),
        new_name=new_name).split("\n")
