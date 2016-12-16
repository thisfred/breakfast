def dedent(code):
    lines = code.split('\n')
    cut = len(lines[-1])
    return '\n'.join(l[cut:] for l in lines)
