from breakfast.source import Source


def dedent(code: str) -> str:
    lines = code.split("\n")
    indentation = len(lines[-1])
    return "\n".join(line[indentation:] for line in lines)


def make_source(
    code: str, module_name: str = "", filename: str | None = None
) -> Source:
    return Source(
        lines=tuple(dedent(code).split("\n")),
        module_name=module_name,
        path=filename or "",
        project_root=".",
    )
