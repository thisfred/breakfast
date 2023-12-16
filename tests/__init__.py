from breakfast.source import Source


def dedent(code: str) -> str:
    lines = code.split("\n")
    indentation = len(lines[-1])
    return "\n".join(line[indentation:] for line in lines)


def make_source(code: str, filename: str | None = None) -> Source:
    return Source(
        lines=tuple(line.encode("utf-8") for line in dedent(code).split("\n")),
        path=filename or "",
        project_root=".",
    )
