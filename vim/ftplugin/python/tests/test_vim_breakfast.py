import vim_breakfast


def test_simple_rename() -> None:
    code = [
        "def fun():",
        "    old = 12",
        "    old2 = 13",
        "    result = old + old2",
        "    del old",
        "    return result",
    ]

    result = [
        l
        for l in vim_breakfast.do_rename(
            root=".", buffer_contents=code, row=1, column=4, new_name="new"
        )
    ]

    assert [
        (1, "    new = 12"),
        (3, "    result = new + old2"),
        (4, "    del new"),
    ] == result
