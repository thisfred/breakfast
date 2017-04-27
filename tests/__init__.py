from breakfast.source import Source


def dedent(code):
    lines = code.split('\n')
    indentation = len(lines[-1])
    return '\n'.join(l[indentation:] for l in lines)


def make_source(code, module_name=''):
    return Source(dedent(code).split('\n'), module_name=module_name)
