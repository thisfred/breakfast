import ast

from collections import ChainMap, defaultdict
from collections.abc import Iterator, MutableMapping
from functools import singledispatch

from breakfast.position import Position
from breakfast.source import Source
from tests import make_source


QualifiedName = tuple[str, ...]
Occurrence = tuple[QualifiedName, Position, bool]


class Env:
    def __init__(self) -> None:
        self.lookup: ChainMap[str, QualifiedName] = ChainMap()
        self.classes: set[QualifiedName] = set()
        self.aliases: dict[QualifiedName, QualifiedName] = {}
        self.rewrites: dict[QualifiedName, QualifiedName] = {}
        self.superclasses: dict[QualifiedName, list[QualifiedName]] = {}
        self.store_lookup = True

    def enter_scope(self, store_lookup: bool = True) -> None:
        self.store_lookup_old = self.store_lookup
        self.store_lookup = store_lookup
        self.lookup = self.lookup.new_child()

    def leave_scope(self) -> None:
        self.lookup = self.lookup.parents
        self.store_lookup = self.store_lookup_old


def generic_visit(
    node: ast.AST,
    source: Source,
    path: QualifiedName,
    env: Env,
) -> Iterator[Occurrence]:
    """Called if no explicit visitor function exists for a node.

    Adapted from NodeVisitor in:

    https://github.com/python/cpython/blob/master/Lib/ast.py
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    yield from visit(item, source, path, env)
        elif isinstance(value, ast.AST):
            yield from visit(value, source, path, env)


@singledispatch
def visit(
    node: ast.AST, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    yield from generic_visit(node, source, path, env)


@visit.register
def visit_module(
    node: ast.Module, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    env.enter_scope()
    yield from generic_visit(node, source, get_module_scope_path(source), env)
    env.leave_scope()


@visit.register
def visit_name(
    node: ast.Name, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    position = node_position(node, source)

    is_definition = False
    if node.id not in env.lookup:
        if isinstance(node.ctx, ast.Store):
            env.lookup[node.id] = path + (node.id,)
            is_definition = True
        else:
            module_scope_path = get_module_scope_path(source)
            env.lookup[node.id] = module_scope_path + (node.id,)
    elif not env.store_lookup and isinstance(node.ctx, ast.Store):
        env.lookup[node.id] = path + (node.id,)
        is_definition = True

    yield (env.lookup[node.id], position, is_definition)


def print_and_yield(iterator):
    for item in iterator:
        print(item)
        yield item


@visit.register
def visit_assign(
    node: ast.Assign, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:

    assert (
        len(node.targets) == 1
    ), f"node targets longer than 1 for {node=}, {node.targets=}"
    yield from print_and_yield(visit(node.targets[0], source, path, env))
    yield from visit(node.value, source, path, env)

    target_names = get_names(node.targets[0], env)
    value_names = get_names(node.value, env)
    for target, value in zip(target_names, value_names, strict=True):
        env.aliases[target + (".",)] = value + (".",)


def get_names(value: ast.AST, env: Env) -> list[QualifiedName]:
    match value:
        case ast.Tuple(elts=elements):
            return [
                i
                for e in elements
                for i in get_names(e, env)  # pylint: disable=not-an-iterable
            ]
        case other:
            return [qualified_name_for(other, env)]


def qualified_name_for(node: ast.AST, env: Env) -> QualifiedName:
    match node:
        case ast.Name(id=name):
            return env.lookup[name]
        case ast.Attribute(value=value, attr=attr):
            return qualified_name_for(value, env) + (attr,)
        case ast.Call(func=function):
            path = qualified_name_for(function, env)
            if path in env.classes:
                return path

            return path + ("()",)
        case _:
            return ()


@visit.register
def visit_attribute(
    node: ast.Attribute, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    yield from visit(node.value, source, path, env)

    position = node_position(node, source)

    names = names_from(node.value)

    new_path = env.lookup[names[0]]

    for name in names[1:]:
        new_path += (name,)
        position = source.find_after(name, position)

    position = source.find_after(node.attr, position)
    new_path += (".",)

    yield new_path + (node.attr,), position, isinstance(node.ctx, ast.Store)


@visit.register
def visit_call(
    node: ast.Call, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    for arg in node.args:
        yield from visit(arg, source, path, env)
    yield from visit(node.func, source, path, env)

    call_position = node_position(node, source)

    names = names_from(node.func)

    new_path = env.lookup[names[0]]

    for name in names[1:]:
        new_path += (name,)

    new_path = new_path + ("()",)

    for keyword in node.keywords:
        if not keyword.arg:
            continue

        keyword_position = source.find_after(keyword.arg, call_position)
        yield new_path + (keyword.arg,), keyword_position, False


@visit.register
def visit_function_definition(
    node: ast.FunctionDef, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    position = node_position(node, source, column_offset=len("def "))
    node_path = path + (node.name,)
    env.lookup[node.name] = node_path
    class_path = node_path[:-2]
    is_method = class_path in env.classes

    yield node_path, position, True
    env.enter_scope()
    new_path = node_path + ("()",)
    for i, arg in enumerate(node.args.args):
        env.lookup[arg.arg] = new_path + (arg.arg,)
        arg_position = node_position(arg, source)
        arg_path = new_path + (arg.arg,)
        yield arg_path, arg_position, True
        if (
            i == 0
            and is_method
            and not is_static_method(node)
            and not is_class_method(node)
        ):
            # self argument
            env.rewrites[arg_path + (".",)] = class_path + (".",)

    yield from generic_visit(node, source, new_path, env)
    env.leave_scope()


def is_static_method(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "staticmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


def is_class_method(node: ast.FunctionDef) -> bool:
    return any(
        n.id == "classmethod" for n in node.decorator_list if isinstance(n, ast.Name)
    )


@visit.register
def visit_class(
    node: ast.ClassDef, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    superclasses = []
    for base in node.bases:
        for base_name, base_position, _ in visit(base, source, path, env):
            yield base_name, base_position, False
        if base_name:
            superclasses.append(base_name)

    node_path = path + (node.name,)
    env.lookup[node.name] = node_path
    position = node_position(node, source, column_offset=len("class "))
    env.classes.add(node_path)
    env.aliases[node_path + ("()", ".")] = node_path + (".",)
    env.superclasses[node_path] = superclasses
    yield node_path, position, True

    new_path = node_path + (".",)
    for statement in node.body:
        yield from visit(statement, source, new_path, env)


def visit_comprehension(
    node: ast.DictComp | ast.ListComp | ast.SetComp | ast.GeneratorExp,
    source: Source,
    path: QualifiedName,
    env: Env,
    *sub_nodes: ast.AST,
) -> Iterator[Occurrence]:
    position = node_position(node, source)
    name = f"{type(node)}-{position.row},{position.column}"

    path += (name,)
    env.enter_scope(store_lookup=False)

    for generator in node.generators:
        yield from visit(generator.target, source, path, env)
        yield from visit(generator.iter, source, path, env)
        for if_node in generator.ifs:
            yield from visit(if_node, source, path, env)

    for sub_node in sub_nodes:
        yield from visit(sub_node, source, path, env)

    env.leave_scope()


@visit.register
def visit_dictionary_comprehension(
    node: ast.DictComp, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    yield from visit_comprehension(node, source, path, env, node.key, node.value)


@visit.register
def visit_list_comprehension(
    node: ast.ListComp, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    yield from visit_comprehension(node, source, path, env, node.elt)


@visit.register
def visit_set_comprehension(
    node: ast.SetComp, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    yield from visit_comprehension(node, source, path, env, node.elt)


@visit.register
def visit_generator_exp(
    node: ast.GeneratorExp, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    yield from visit_comprehension(node, source, path, env, node.elt)


@visit.register
def visit_global(
    node: ast.Global, source: Source, path: QualifiedName, env: Env
) -> Iterator[Occurrence]:
    position = node_position(node, source)
    module_scope_path = get_module_scope_path(source)
    for name in node.names:
        position = source.find_after(name, position)
        qualified_name = module_scope_path + (name,)
        env.lookup[name] = qualified_name
        yield qualified_name, position, False


@singledispatch
def names_from(node: ast.AST) -> QualifiedName:  # pylint: disable=unused-argument
    return ()


@names_from.register
def name_names(node: ast.Name) -> QualifiedName:
    return (node.id,)


@names_from.register
def attribute_names(node: ast.Attribute) -> QualifiedName:
    return names_from(node.value) + (
        ".",
        node.attr,
    )


@names_from.register
def call_names(node: ast.Call) -> QualifiedName:
    names = names_from(node.func) + ("()",)
    return names


def get_module_scope_path(source: Source) -> QualifiedName:
    module_path: QualifiedName = ()
    for part in source.module_name.split("."):
        module_path += (part, ".")

    return module_path


def node_position(
    node: ast.AST, source: Source, row_offset: int = 0, column_offset: int = 0
) -> Position:
    return source.position(
        row=(node.lineno - 1) + row_offset, column=node.col_offset + column_offset
    )


def all_occurrence_positions(start_position: Position) -> list[Position]:
    found: QualifiedName | None = None
    occurrences: MutableMapping[
        QualifiedName, list[tuple[Position, bool]]
    ] = defaultdict(list)
    source = start_position.source

    env = Env()
    for qualified_name, position, is_definition in visit(
        source.get_ast(), source, path=(), env=env
    ):
        occurrences[qualified_name].append((position, is_definition))
        if position == start_position:
            found = qualified_name

    if found:
        found = rewrite_occurrences(occurrences, found, env)

    from pprint import pprint

    # pprint(env.lookup)
    pprint(occurrences)
    # pprint(env.superclasses)
    # pprint(env.classes)
    pprint(env.aliases)
    # for other in other_sources or []:
    #     visit(other.get_ast(), other.path, env=env)
    if found:
        return sorted(pos for pos, _ in occurrences[found])

    return []


def rewrite_occurrences(
    occurrences: MutableMapping[QualifiedName, list[tuple[Position, bool]]],
    found: QualifiedName,
    env: Env,
) -> QualifiedName:
    while True:
        dirty = False
        possible_matches = {k: v for k, v in occurrences.items() if k[-1] == found[-1]}
        from pprint import pprint

        print("possible_matches")
        pprint(possible_matches)

        for qualified_name, positions in possible_matches.items():
            for prefix, replacement in env.rewrites.items():
                length = len(prefix)
                if qualified_name[:length] == prefix:
                    alternative = replacement + qualified_name[length:]
                    occurrences[alternative] += occurrences[qualified_name]
                    del occurrences[qualified_name]
                    if qualified_name == found:
                        found = alternative
                    dirty = True

        for qualified_name, positions in possible_matches.items():
            if any(is_definition for _, is_definition in positions):
                continue

            for alternative in alternatives_for(qualified_name, env):
                print(f"{alternative=}")
                if alternative not in possible_matches:
                    continue

                if not any(
                    is_definition for _, is_definition in possible_matches[alternative]
                ):
                    continue

                print("here")
                occurrences[alternative] += occurrences[qualified_name]
                del occurrences[qualified_name]
                if qualified_name == found:
                    found = alternative
                dirty = True
                break

        if not dirty:
            break

    return found


def alternatives_for(
    qualified_name: QualifiedName, env: Env, seen: set[QualifiedName] | None = None
) -> Iterator[QualifiedName]:
    seen = seen or set()
    print(f"{qualified_name=}")
    for prefix, replacement in env.aliases.items():
        length = len(prefix)
        if qualified_name[:length] == prefix:
            alternative = replacement + qualified_name[length:]
            if alternative in seen:
                continue
            seen.add(alternative)
            yield alternative
            yield from alternatives_for(alternative, env, seen)
    for prefix, replacements in env.superclasses.items():
        for replacement in replacements:
            length = len(prefix)
            if qualified_name[:length] == prefix:
                alternative = replacement + qualified_name[length:]
                if alternative in seen:
                    continue
                seen.add(alternative)
                yield alternative
                yield from alternatives_for(alternative, env, seen)


def test_finds_global_variable() -> None:
    source = make_source(
        """
    var = 12

    def fun():
        global var
        var = 20
    """
    )

    position = Position(source, 1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 11),
        Position(source, 5, 4),
    ]


def test_distinguishes_local_variables_from_global() -> None:
    source = make_source(
        """
        def fun():
            var = 12
            var2 = 13
            result = var + var2
            del var
            return result

        var = 20
        """
    )

    position = source.position(row=2, column=4)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=4),
        source.position(row=4, column=13),
        source.position(row=5, column=8),
    ]


def test_finds_non_local_variable() -> None:
    source = make_source(
        """
    var = 12

    def fun():
        result = var + 1
        return result

    var = 20
    """
    )

    position = source.position(1, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 1, 0),
        Position(source, 4, 13),
        Position(source, 7, 0),
    ]


def test_finds_non_local_variable_defined_after_use() -> None:
    source = make_source(
        """
    def fun():
        result = var + 1
        return result

    var = 20
    """
    )

    position = source.position(5, 0)

    assert all_occurrence_positions(position) == [
        Position(source, 2, 13),
        Position(source, 5, 0),
    ]


def test_does_not_rename_random_attributes() -> None:
    source = make_source(
        """
        import os

        path = os.path.dirname(__file__)
        """
    )

    position = source.position(row=3, column=0)

    assert all_occurrence_positions(position) == [source.position(row=3, column=0)]


def test_finds_parameter() -> None:
    source = make_source(
        """
        def fun(arg=1):
            print(arg)

        arg = 8
        fun(arg=arg)
        """
    )

    assert all_occurrence_positions(source.position(1, 8)) == [
        source.position(1, 8),
        source.position(2, 10),
        source.position(5, 4),
    ]


def test_finds_function() -> None:
    source = make_source(
        """
        def fun():
            return 'result'
        result = fun()
        """
    )

    assert [source.position(1, 4), source.position(3, 9)] == all_occurrence_positions(
        source.position(1, 4)
    )


def test_finds_class() -> None:
    source = make_source(
        """
        class Class:
            pass

        instance = Class()
        """
    )

    assert [source.position(1, 6), source.position(4, 11)] == all_occurrence_positions(
        source.position(1, 6)
    )


def test_finds_method_name() -> None:
    source = make_source(
        """
        class A:

            def method(self):
                pass

        unbound = A.method
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=8),
        source.position(row=6, column=12),
    ]


def test_finds_passed_argument() -> None:
    source = make_source(
        """
        var = 2
        def fun(arg, arg2):
            return arg + arg2
        fun(1, var)
        """
    )

    assert [source.position(1, 0), source.position(4, 7)] == all_occurrence_positions(
        source.position(1, 0)
    )


def test_finds_parameter_with_unusual_indentation() -> None:
    source = make_source(
        """
        def fun(arg, arg2):
            return arg + arg2
        fun(
            arg=\\
                1,
            arg2=2)
        """
    )

    assert [
        source.position(1, 8),
        source.position(2, 11),
        source.position(4, 4),
    ] == all_occurrence_positions(source.position(1, 8))


def test_does_not_find_method_of_unrelated_class() -> None:
    source = make_source(
        """
        class ClassThatShouldHaveMethodRenamed:

            def method(self, arg):
                pass

            def foo(self):
                self.method('whatever')


        class UnrelatedClass:

            def method(self, arg):
                pass

            def foo(self):
                self.method('whatever')


        a = ClassThatShouldHaveMethodRenamed()
        a.method()
        b = UnrelatedClass()
        b.method()
        """
    )

    occurrences = all_occurrence_positions(source.position(3, 8))

    assert [
        source.position(3, 8),
        source.position(7, 13),
        source.position(20, 2),
    ] == occurrences


def test_finds_definition_from_call() -> None:
    source = make_source(
        """
        def fun():
            pass

        def bar():
            fun()
        """
    )

    assert [source.position(1, 4), source.position(5, 4)] == all_occurrence_positions(
        source.position(1, 4)
    )


def test_finds_attribute_assignments() -> None:
    source = make_source(
        """
        class ClassName:

            def __init__(self, property):
                self.property = property

            def get_property(self):
                return self.property
        """
    )
    occurrences = all_occurrence_positions(source.position(4, 13))

    assert [source.position(4, 13), source.position(7, 20)] == occurrences


def test_finds_dict_comprehension_variables() -> None:
    source = make_source(
        """
        var = 1
        foo = {var: None for var in range(100) if var % 3}
        var = 2
        """
    )

    position = source.position(row=2, column=21)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=21),
        source.position(row=2, column=42),
    ]


def test_finds_list_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = [
            var for var in range(100) if var % 3]
        var = 200
        """
    )

    position = source.position(row=3, column=12)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=4),
        source.position(row=3, column=12),
        source.position(row=3, column=33),
    ]


def test_finds_set_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = {var for var in range(100) if var % 3}
        """
    )

    position = source.position(row=2, column=15)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    ]


def test_finds_generator_comprehension_variables() -> None:
    source = make_source(
        """
        var = 100
        foo = (var for var in range(100) if var % 3)
        """
    )

    position = source.position(row=2, column=15)

    assert all_occurrence_positions(position) == [
        source.position(row=2, column=7),
        source.position(row=2, column=15),
        source.position(row=2, column=36),
    ]


def test_finds_loop_variables() -> None:
    source = make_source(
        """
        var = None
        for i, var in enumerate(['foo']):
            print(i)
            print(var)
        print(var)
        """
    )

    position = source.position(row=1, column=0)

    assert all_occurrence_positions(position) == [
        source.position(row=1, column=0),
        source.position(row=2, column=7),
        source.position(row=4, column=10),
        source.position(row=5, column=6),
    ]


def test_finds_tuple_unpack() -> None:
    source = make_source(
        """
    foo, var = 1, 2
    print(var)
    """
    )

    position = source.position(row=1, column=5)

    assert all_occurrence_positions(position) == [
        source.position(1, 5),
        source.position(2, 6),
    ]


def test_finds_superclasses() -> None:
    source = make_source(
        """
        class A:

            def method(self):
                pass

        class B(A):
            pass

        b = B()
        c = b
        c.method()
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(row=3, column=8),
        source.position(row=11, column=2),
    ]


def test_recognizes_multiple_assignments() -> None:
    source = make_source(
        """
    class A:
        def method(self):
            pass

    class B:
        def method(self):
            pass

    foo, bar = A(), B()
    foo.method()
    bar.method()
    """
    )

    position = source.position(row=2, column=8)

    assert all_occurrence_positions(position) == [
        source.position(2, 8),
        source.position(10, 4),
    ]


def test_finds_enclosing_scope_variable_from_comprehension() -> None:
    source = make_source(
        """
    var = 3
    res = [foo for foo in range(100) if foo % var]
    """
    )

    position = source.position(row=1, column=0)

    assert all_occurrence_positions(position) == [
        source.position(1, 0),
        source.position(2, 42),
    ]


def test_finds_static_method() -> None:
    source = make_source(
        """
        class A:

            @staticmethod
            def method(arg):
                pass

        a = A()
        b = a.method('foo')
        """
    )

    position = source.position(row=4, column=8)

    assert all_occurrence_positions(position) == [
        source.position(4, 8),
        source.position(8, 6),
    ]


def test_finds_method_after_call() -> None:
    source = make_source(
        """
        class A:

            def method(arg):
                pass

        b = A().method('foo')
        """
    )

    position = source.position(row=3, column=8)

    assert all_occurrence_positions(position) == [
        source.position(3, 8),
        source.position(6, 8),
    ]


def test_finds_argument() -> None:
    source = make_source(
        """
        class A:

            def foo(self, arg):
                print(arg)

            def bar(self):
                arg = "1"
                self.foo(arg=arg)
        """
    )

    position = source.position(row=3, column=18)

    assert all_occurrence_positions(position) == [
        source.position(3, 18),
        source.position(4, 14),
        source.position(8, 17),
    ]


def test_finds_method_but_not_function() -> None:
    source = make_source(
        """
        class A:

            def old(self):
                pass

            def foo(self):
                self.old()

            def bar(self):
                old()

        def old():
            pass
        """
    )
    position = source.position(3, 8)

    assert all_occurrence_positions(position) == [
        source.position(3, 8),
        source.position(7, 13),
    ]
