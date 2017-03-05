from breakfast.source import Source


def dedent(code):
    lines = code.split('\n')
    cut = len(lines[-1])
    return '\n'.join(l[cut:] for l in lines)


def make_source(code):
    return Source(dedent(code).split('\n'))
