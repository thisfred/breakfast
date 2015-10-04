"""Tests for rename refactoring."""

import breakfast

source = """\
def fun():
    foo = 12
    foo2 = 13
    result = foo + foo2
    del foo
    return result
"""

target = """\
def fun():
    bar = 12
    foo2 = 13
    result = bar + foo2
    del bar
    return result
"""


def test_renames_local_variable_in_func():
    assert target.strip() == breakfast.rename_variable(
        source=source, old_name='foo', new_name='bar').strip()
