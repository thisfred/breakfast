from typing import Optional

from breakfast.source import Source


def dedent(code: str) -> str:
    lines = code.split("\n")
    indentation = len(lines[-1])
    return "\n".join(l[indentation:] for l in lines)


def make_source(
    code: str, module_name: str = "", file_name: Optional[str] = None
) -> Source:
    return Source(
        dedent(code).split("\n"), module_name=module_name, file_name=file_name
    )
