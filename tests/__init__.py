from breakfast.source import Source


def dedent(code):
    lines = code.split('\n')
    cut = len(lines[-1])
    return '\n'.join(l[cut:] for l in lines)


def make_source(code, module_name=None):
    return Source(dedent(code).split('\n'), module_name=module_name)
