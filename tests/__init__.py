def dedent(code: str, *, by: int=4) -> str:
    return '\n'.join(l[by:] for l in code.split('\n'))
