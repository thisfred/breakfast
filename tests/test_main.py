from breakfast.main import Application
from breakfast.source import Source


def test_renames_function_from_lines():
    source = Source([
        "def fun_old():",
        "    return 'result'",
        "result = fun_old()"])
    application = Application(source)

    application.rename(row=0, column=4, new_name='fun_new')

    assert list(source.get_changes()) == [
        (0, "def fun_new():"),
        (2, "result = fun_new()")]
