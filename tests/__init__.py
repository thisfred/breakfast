def dedent(code, by=4):
    return '\n'.join(l[by:] for l in code.split('\n'))
