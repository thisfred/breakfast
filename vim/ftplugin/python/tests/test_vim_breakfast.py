import vim_breakfast


def test_simple_rename():
    code = [
        "def fun():",
        "    old = 12",
        "    old2 = 13",
        "    result = old + old2",
        "    del old",
        "    return result"]

    result = vim_breakfast.do_rename(
        buffer_contents=code,
        old_name="old",
        row=1,
        column=4,
        new_name="new")

    assert [
        "def fun():",
        "    new = 12",
        "    old2 = 13",
        "    result = new + old2",
        "    del new",
        "    return result"] == result
