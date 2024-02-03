from dataclasses import dataclass

from breakfast import types


@dataclass
class Edit:
    start: types.Position
    end: types.Position
    text: str


def extract_variable(
    name: str, start: types.Position, end: types.Position
) -> tuple[Edit, ...]:
    print(f"{start=}, {end=}")
    extracted = start.text_through(end)
    return (
        Edit(
            start=start.start_of_line,
            end=start.start_of_line,
            text=f"{name} = {extracted}\n",
        ),
        Edit(start=start, end=end, text=name),
    )
