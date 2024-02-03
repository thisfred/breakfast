from breakfast.refactoring.extract import Edit, extract_variable

from tests import make_source


def test_introduce_variable_should_insert_name_definition():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 8)

    insert, *_ = extract_variable(name="b", start=extraction_start, end=extraction_end)
    assert insert.text == "b = a + 3\n"


def test_introduce_variable_should_replace_extracted_test_with_new_name():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 8)

    _, replace = extract_variable(name="b", start=extraction_start, end=extraction_end)
    assert replace == Edit(start=extraction_start, end=extraction_end, text="b")


def test_introduce_variable_should_insert_name_definition_before_extraction_point():
    source = make_source(
        """
        a = a + 3
        """
    )
    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 8)

    insert, *_ = extract_variable(name="b", start=extraction_start, end=extraction_end)

    assert insert.start < extraction_start


def test_introduce_variable_should_replace_code_with_variable():
    source = make_source(
        """
        a = some_calculation() + 3
        """
    )

    extraction_start = source.position(1, 4)
    extraction_end = source.position(1, 21)
    edits = extract_variable(name="result", start=extraction_start, end=extraction_end)

    assert edits == (
        Edit(
            source.position(1, 0),
            source.position(1, 0),
            "result = some_calculation()\n",
        ),
        Edit(extraction_start, extraction_end, "result"),
    )
