import ast
import logging
import sys
from collections.abc import Iterable, Iterator, Sequence
from functools import singledispatch
from itertools import repeat
from typing import Protocol

from breakfast.visitor import generic_visit

logger = logging.getLogger(__name__)

NEWLINE = "\n"
INDENTATION = "    "

COMPARISONS = {
    ast.Eq: "==",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.In: "in",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.NotEq: "!=",
    ast.NotIn: "not in",
}
BINARY_OPERATORS = {
    ast.Add: "+",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.LShift: "<<",
    ast.MatMult: "@",
    ast.Mod: "%",
    ast.Mult: "*",
    ast.Pow: "**",
    ast.RShift: ">>",
    ast.Sub: "-",
}

PRECEDENCE: dict[type[ast.AST], int] = {
    ast.Await: 0,
    ast.Pow: 1,
    ast.UAdd: 2,
    ast.USub: 2,
    ast.Invert: 2,
    ast.Mult: 3,
    ast.MatMult: 3,
    ast.Div: 3,
    ast.FloorDiv: 3,
    ast.Mod: 3,
    ast.Add: 4,
    ast.Sub: 4,
    ast.LShift: 5,
    ast.RShift: 5,
    ast.BitAnd: 6,
    ast.BitXor: 7,
    ast.BitOr: 8,
    ast.Compare: 9,
    ast.Not: 10,
    ast.And: 11,
    ast.Or: 12,
}

UNARY_OPERATORS = {
    ast.UAdd: "+",
    ast.USub: "-",
    ast.Not: "not ",
    ast.Invert: "~",
}
BOOLEAN_OPERATORS = {ast.And: " and ", ast.Or: " or "}
FORMATTING_CONVERSIONS = {-1: "", 114: "!r"}


class NodeWithElse(Protocol):
    orelse: list[ast.stmt]


def unparse(node: ast.AST) -> str:
    return "".join(to_source(node, level=0)).strip()


@singledispatch
def get_precedence(node: ast.AST) -> int | None:
    return PRECEDENCE.get(type(node))


@get_precedence.register
def in_op(node: ast.BinOp | ast.BoolOp | ast.UnaryOp) -> int | None:
    return get_precedence(node.op)


@singledispatch
def to_source(node: ast.AST, level: int) -> Iterator[str]:
    logger.warning(f"Unhandled node type: {node}")
    yield from generic_visit(to_source, node, level)


@to_source.register
def name(node: ast.Name, level: int) -> Iterator[str]:
    yield node.id


@to_source.register
def expr(node: ast.Expr, level: int) -> Iterator[str]:
    yield from to_source(node.value, level)


@to_source.register
def bin_op(node: ast.BinOp, level: int) -> Iterator[str]:
    yield from maybe_parenthesize(node.left, node.op, level)
    yield f" {BINARY_OPERATORS[type(node.op)]} "
    yield from maybe_parenthesize(node.right, node.op, level)


def maybe_parenthesize(node: ast.AST, op: ast.AST, level: int) -> Iterator[str]:
    node_precedence = get_precedence(node)
    op_precedence = get_precedence(op)
    parenthesize = (
        node_precedence is not None
        and op_precedence is not None
        and node_precedence > op_precedence
    )
    print(
        f"{type(node)=}: {PRECEDENCE.get(type(node))=}, {type(op)=}: {PRECEDENCE.get(type(op))=}"
    )
    if parenthesize:
        yield "("
    yield from to_source(node, level)
    if parenthesize:
        yield ")"


@to_source.register
def unary_op(node: ast.UnaryOp, level: int) -> Iterator[str]:
    yield f"{UNARY_OPERATORS[type(node.op)]}"
    yield from maybe_parenthesize(node.operand, node.op, level)


@to_source.register
def slice_node(node: ast.Slice, level: int) -> Iterator[str]:
    if node.lower:
        yield from to_source(node.lower, level)
    yield ":"
    if node.upper:
        yield from to_source(node.upper, level)
    if node.step:
        yield ":"
        yield from to_source(node.step, level)


@to_source.register
def bool_op(node: ast.BoolOp, level: int) -> Iterator[str]:
    op = f"{BOOLEAN_OPERATORS[type(node.op)]}"
    for i, sub_node in enumerate(node.values):
        if i > 0:
            yield op
        yield from maybe_parenthesize(sub_node, node, level)


@to_source.register
def subscript(node: ast.Subscript, level: int) -> Iterator[str]:
    yield from to_source(node.value, level)
    yield "["
    yield from to_source(node.slice, level)
    yield "]"


@to_source.register
def tuple_node(node: ast.Tuple, level: int) -> Iterator[str]:
    yield "("
    yield from with_separators(
        node.elts, level, include_final_separator=len(node.elts) == 1
    )
    yield ")"


@to_source.register
def yield_node(node: ast.Yield, level: int) -> Iterator[str]:
    yield "yield"
    if node.value is None:
        return
    yield " "
    yield from to_source(node.value, level)


@to_source.register
def yield_from_node(node: ast.YieldFrom, level: int) -> Iterator[str]:
    yield "yield from "
    yield from to_source(node.value, level)


@to_source.register
def attribute(node: ast.Attribute, level: int) -> Iterator[str]:
    yield from to_source(node.value, level)
    yield f".{node.attr}"


@to_source.register
def assign(node: ast.Assign, level: int) -> Iterator[str]:
    for target in node.targets:
        yield from to_source(target, level)
        yield " = "
    yield from to_source(node.value, level)


@to_source.register
def aug_assign(node: ast.AugAssign, level: int) -> Iterator[str]:
    yield from to_source(node.target, level)
    yield f" {BINARY_OPERATORS[type(node.op)]}= "
    yield from to_source(node.value, level)


@to_source.register
def ann_assign(node: ast.AnnAssign, level: int) -> Iterator[str]:
    yield from to_source(node.target, level)
    yield from render_annotation(node, level)
    if node.value:
        yield " = "
        yield from to_source(node.value, level)


@to_source.register
def call(node: ast.Call, level: int) -> Iterator[str]:
    yield from to_source(node.func, level)
    yield "("

    yield from with_separators(
        node.args, level, include_final_separator=bool(node.keywords)
    )
    for keyword, comma in zip(
        node.keywords, separators(node.keywords), strict=True
    ):
        if keyword.arg:
            yield f"{keyword.arg}="
            yield from to_source(keyword.value, level)
            yield comma
        else:
            yield from to_source(keyword, level)
    yield ")"


@to_source.register
def keyword(node: ast.keyword, level: int) -> Iterator[str]:
    yield "**"
    yield from to_source(node.value, level)


@to_source.register
def import_node(node: ast.Import, level: int) -> Iterator[str]:
    yield "import "
    yield ", ".join(alias.name for alias in node.names)


@to_source.register
def import_from(node: ast.ImportFrom, level: int) -> Iterator[str]:
    yield f"from {('.' * node.level) + node.module if node.module else ''} import "
    yield ", ".join(alias.name for alias in node.names)


@to_source.register
def compare(node: ast.Compare, level: int) -> Iterator[str]:
    yield from maybe_parenthesize(node.left, node.ops[0], level)
    for op, comparator in zip(node.ops, node.comparators, strict=True):
        yield f" {COMPARISONS[type(op)]} "
        yield from maybe_parenthesize(comparator, op, level)


@to_source.register
def pass_node(node: ast.Pass, level: int) -> Iterator[str]:
    yield "pass"


@to_source.register
def break_node(node: ast.Break, level: int) -> Iterator[str]:
    yield "break"


@to_source.register
def continue_node(node: ast.Continue, level: int) -> Iterator[str]:
    yield "continue"


@to_source.register
def starred(node: ast.Starred, level: int) -> Iterator[str]:
    yield "*"
    yield from to_source(node.value, level)


@to_source.register
def assert_node(node: ast.Assert, level: int) -> Iterator[str]:
    yield "assert "
    yield from to_source(node.test, level)
    if node.msg:
        yield ", "
        yield from to_source(node.msg, level)


@to_source.register
def module(node: ast.Module, level: int) -> Iterator[str]:
    yield from render_body(node.body, level)


@to_source.register
def if_node(node: ast.If, level: int) -> Iterator[str]:
    yield "if "
    yield from to_source(node.test, level)
    yield ":"
    yield from render_body(node.body, level + 1)
    yield from render_else_or_elif(node, level)


@to_source.register
def for_node(node: ast.For, level: int) -> Iterator[str]:
    yield "for "
    yield from to_source(node.target, level)
    yield " in "
    yield from to_source(node.iter, level)
    yield ":"
    yield from render_body(node.body, level + 1)
    yield from render_else(node, level)


@to_source.register
def while_node(node: ast.While, level: int) -> Iterator[str]:
    yield "while "
    yield from to_source(node.test, level)
    yield ":"
    yield from render_body(node.body, level + 1)
    yield from render_else(node, level)


@to_source.register
def generator_exp(node: ast.GeneratorExp, level: int) -> Iterator[str]:
    yield "("
    yield from render_comprehension(node, level)
    yield ")"


@to_source.register
def set_comp(node: ast.SetComp, level: int) -> Iterator[str]:
    yield "{"
    yield from render_comprehension(node, level)
    yield "}"


@to_source.register
def list_comp(node: ast.ListComp, level: int) -> Iterator[str]:
    yield "["
    yield from render_comprehension(node, level)
    yield "]"


@to_source.register
def dict_comp(node: ast.DictComp, level: int) -> Iterator[str]:
    yield "{"
    yield from to_source(node.key, level)
    yield ": "
    yield from to_source(node.value, level)
    yield from render_generators(node.generators, level)
    yield "}"


@to_source.register
def return_node(node: ast.Return, level: int) -> Iterator[str]:
    yield "return"
    if node.value is None:
        return
    yield " "
    yield from to_source(node.value, level)


@to_source.register
def raise_node(node: ast.Raise, level: int) -> Iterator[str]:
    yield "raise"
    if node.exc:
        yield " "
        yield from to_source(node.exc, level)


@to_source.register
def async_function_def(node: ast.AsyncFunctionDef, level: int) -> Iterator[str]:
    yield from render_function_definition(node, level, prefix="async ")


@to_source.register
def function_def(node: ast.FunctionDef, level: int) -> Iterator[str]:
    yield from render_function_definition(node, level)


def render_function_definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef, level: int, prefix: str = ""
) -> Iterator[str]:
    yield from render_decorators(node.decorator_list, level)
    yield start_line(f"{prefix}def {node.name}", level)
    yield from render_type_parameters(node, level)
    yield "("
    yield from to_source(node.args, level)
    yield ")"
    yield from render_returns(node, level)
    yield ":"
    yield from render_body(node.body, level + 1)


@to_source.register
def await_node(node: ast.Await, level: int) -> Iterator[str]:
    yield "await "
    yield from to_source(node.value, level)


@to_source.register
def lambda_node(node: ast.Lambda, level: int) -> Iterator[str]:
    yield "lambda "
    yield from to_source(node.args, level)
    yield ": "
    yield from to_source(node.body, level)


@to_source.register
def arguments(node: ast.arguments, level: int) -> Iterator[str]:
    yield from render_position_only_args(node, level)
    yield from render_args(node, level)
    yield from render_vararg(node, level)
    yield from render_keyword_only_args(node, level)
    yield from render_kwarg(node, level)


@to_source.register
def arg(node: ast.arg, level: int) -> Iterator[str]:
    yield node.arg
    yield from render_annotation(node, level)


@to_source.register
def type_var(node: ast.TypeVar, level: int) -> Iterator[str]:
    yield node.name
    if node.bound:
        yield ": "
        yield from to_source(node.bound, level)


@to_source.register
def class_def(node: ast.ClassDef, level: int) -> Iterator[str]:
    yield from render_decorators(node.decorator_list, level)
    yield start_line(f"class {node.name}", level)
    yield from render_type_parameters(node, level)

    if node.bases or node.keywords:
        yield "("
        yield from with_separators(node.bases, level)
        yield ")"
    yield ":"
    yield from render_body(node.body, level + 1)


@to_source.register
def try_node(node: ast.Try, level: int) -> Iterator[str]:
    yield start_line("try:", level)
    yield from render_body(node.body, level + 1)
    for handler in node.handlers:
        yield from to_source(handler, level)
    yield from render_else(node, level)
    if node.finalbody:
        yield start_line("finally:", level)
        yield from render_body(node.finalbody, level + 1)


@to_source.register
def except_handler(node: ast.ExceptHandler, level: int) -> Iterator[str]:
    yield start_line("except", level)
    if node.type:
        yield " "
        yield from to_source(node.type, level)
    yield ":"
    yield from render_body(node.body, level + 1)


@to_source.register
def with_node(node: ast.With, level: int) -> Iterator[str]:
    yield start_line("with ", level)
    yield from with_separators(node.items, level)
    yield ":"
    yield from render_body(node.body, level + 1)


@to_source.register
def withitem(node: ast.withitem, level: int) -> Iterator[str]:
    yield from to_source(node.context_expr, level)

    if node.optional_vars:
        yield " as "
        yield from to_source(node.optional_vars, level)


@to_source.register
def named_expr(node: ast.NamedExpr, level: int) -> Iterator[str]:
    yield "("
    yield from to_source(node.target, level)
    yield ":="
    yield from to_source(node.value, level)
    yield ")"


@to_source.register
def match(node: ast.Match, level: int) -> Iterator[str]:
    yield start_line("match ", level)
    yield from to_source(node.subject, level)
    yield ":"
    level += 1
    for case in node.cases:
        yield from to_source(case, level + 1)


@to_source.register
def match_case(node: ast.match_case, level: int) -> Iterator[str]:
    yield start_line("case ", level)
    yield from to_source(node.pattern, level)
    if node.guard:
        yield "if "
        yield from to_source(node.guard, level)
    yield ":"
    yield from render_body(node.body, level + 1)


@to_source.register
def match_as(node: ast.MatchAs, level: int) -> Iterator[str]:
    if node.pattern:
        yield from to_source(node.pattern, level)
        yield " as "
    yield node.name if node.name else "_"


@to_source.register
def match_class(node: ast.MatchClass, level: int) -> Iterator[str]:
    yield from to_source(node.cls, level)
    yield "("
    yield from with_separators(
        node.patterns, level, include_final_separator=bool(node.kwd_attrs)
    )
    for keyword_attribute, keyword_pattern, comma in zip(
        node.kwd_attrs,
        node.kwd_patterns,
        separators(node.kwd_attrs),
        strict=True,
    ):
        yield f"{keyword_attribute}="
        yield from to_source(keyword_pattern, level)
        yield comma
    yield ")"


@to_source.register
def match_star(node: ast.MatchStar, level: int) -> Iterator[str]:
    yield "*_"


@to_source.register
def match_sequence(node: ast.MatchSequence, level: int) -> Iterator[str]:
    yield "["
    yield from with_separators(node.patterns, level)
    yield "]"
    yield from ()


@to_source.register
def match_mapping(node: ast.MatchMapping, level: int) -> Iterator[str]:
    yield "{"
    for key, value, comma in zip(
        node.keys, node.patterns, separators(node.keys), strict=True
    ):
        if key and value:
            yield from to_source(key, level)
            yield ": "
            yield from to_source(value, level)
            yield comma
    yield "}"
    yield from ()


@to_source.register
def joined_str(node: ast.JoinedStr, level: int) -> Iterator[str]:
    strings = [
        part for value in node.values for part in to_source(value, level)
    ]
    f_string = any(s.startswith('f"') for s in strings)
    yield 'f"' if f_string else '"'
    for string in strings:
        if string.startswith('f"'):
            yield string[2:-1]
        else:
            if f_string:
                string = string.replace("{", "{{")
                string = string.replace("}", "}}")
            yield string[1:-1]
    yield '"'


@to_source.register
def if_exp(node: ast.IfExp, level: int) -> Iterator[str]:
    yield "("
    yield from to_source(node.body, level)
    yield " if "
    yield from to_source(node.test, level)
    if node.orelse:
        yield " else "
        yield from to_source(node.orelse, level)
    yield ")"


@to_source.register
def formatted_value(node: ast.FormattedValue, level: int) -> Iterator[str]:
    try:
        conversion = FORMATTING_CONVERSIONS[node.conversion]
    except AttributeError:
        conversion = ""
        logger.warning(
            f"{ast.dump(node)=} did not have conversion\npython version: {sys.version}"
        )
    except KeyError:
        conversion = ""
        logger.warning(f"{node.conversion=} not handled")

    format_spec = (
        ":" + "".join(to_source(node.format_spec, level)).strip('"')
        if node.format_spec
        else ""
    )
    yield "".join(
        ('f"{', *to_source(node.value, level), format_spec, conversion, '}"')
    )


@to_source.register
def list_node(node: ast.List, level: int) -> Iterator[str]:
    yield "["
    yield from with_separators(node.elts, level)
    yield "]"


@to_source.register
def set_node(node: ast.Set, level: int) -> Iterator[str]:
    yield "{"
    yield from with_separators(node.elts, level)
    yield "}"


@to_source.register
def dict_node(node: ast.Dict, level: int) -> Iterator[str]:
    yield "{"
    for key, value, comma in zip(
        node.keys, node.values, separators(node.keys), strict=True
    ):
        if key and value:
            yield from to_source(key, level)
            yield ": "
            yield from to_source(value, level)
            yield comma
    yield "}"


@to_source.register
def constant(node: ast.Constant, level: int) -> Iterator[str]:
    if node.value is Ellipsis:
        yield "..."
    elif isinstance(node.value, str):
        result = repr(node.value)
        if '"' not in result:
            result = result.replace("'", '"')
        yield result
    else:
        yield repr(node.value)


def render_body(statements: Sequence[ast.AST], level: int) -> Iterator[str]:
    for statement in statements:
        yield start_line("", level)
        yield from to_source(statement, level)


def render_else_or_elif(node: NodeWithElse, level: int) -> Iterator[str]:
    if not node.orelse:
        return
    if len(node.orelse) == 1 and isinstance(orelse := node.orelse[0], ast.If):
        yield start_line("elif ", level)
        yield from to_source(orelse.test, level)
        yield ":"
        yield from render_body(orelse.body, level + 1)
        yield from render_else_or_elif(orelse, level)
    else:
        yield from render_else(node, level)


def render_else(node: NodeWithElse, level: int) -> Iterator[str]:
    if not node.orelse:
        return
    yield start_line("else:", level)
    yield from render_body(node.orelse, level + 1)


def render_position_only_args(node: ast.arguments, level: int) -> Iterator[str]:
    if not node.posonlyargs:
        return
    yield from with_separators(
        node.posonlyargs,
        level,
        include_final_separator=bool(node.args or node.kwonlyargs),
    )
    yield ", /, "


def render_args(node: ast.arguments, level: int) -> Iterator[str]:
    if not node.args:
        return
    defaults = (
        *repeat(
            None,
            len(node.args) - len(node.defaults),
        ),
        *node.defaults,
    )
    for arg, default, comma in zip(
        node.args,
        defaults[: len(node.args)],
        separators(
            node.args,
            include_final_comma=bool(node.kwonlyargs or node.vararg),
        ),
        strict=True,
    ):
        yield from to_source(arg, level)
        if default:
            yield "="
            yield from to_source(default, level)
        yield comma


def render_type_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, level: int
) -> Iterator[str]:
    if not node.type_params:
        return
    yield "["
    yield from with_separators(node.type_params, level)
    yield "]"


def render_vararg(node: ast.arguments, level: int) -> Iterator[str]:
    if not node.vararg:
        return
    arg = node.vararg
    yield "*"
    yield from to_source(arg, level)
    if node.kwonlyargs or node.kwarg:
        yield ", "


def render_kwarg(node: ast.arguments, level: int) -> Iterator[str]:
    if not node.kwarg:
        return
    arg = node.kwarg
    yield "**"
    yield from to_source(arg, level)


def render_keyword_only_args(node: ast.arguments, level: int) -> Iterator[str]:
    if not node.kwonlyargs:
        return
    yield "*, "
    defaults = (
        *repeat(
            None,
            len(node.kwonlyargs) - len(node.kw_defaults),
        ),
        *node.kw_defaults,
    )
    for arg, default, comma in zip(
        node.kwonlyargs, defaults, separators(node.kwonlyargs), strict=True
    ):
        yield from to_source(arg, level)
        if default:
            yield "="
            yield from to_source(default, level)
        yield comma


def render_annotation(
    node: ast.arg | ast.AnnAssign, level: int
) -> Iterator[str]:
    if node.annotation:
        yield ": "
        yield from to_source(node.annotation, level)


def render_returns(
    node: ast.FunctionDef | ast.AsyncFunctionDef, level: int
) -> Iterator[str]:
    if not node.returns:
        return
    yield " -> "
    yield from to_source(node.returns, level)


def render_decorators(
    decorators: Iterable[ast.AST], level: int
) -> Iterator[str]:
    for decorator in decorators:
        yield start_line("@", level)
        yield from to_source(decorator, level)


def render_comprehension(
    node: ast.GeneratorExp | ast.ListComp | ast.SetComp, level: int
) -> Iterator[str]:
    yield from to_source(node.elt, level)
    yield from render_generators(node.generators, level)


def render_generators(
    generators: list[ast.comprehension], level: int
) -> Iterator[str]:
    for generator in generators:
        yield " for "
        yield from to_source(generator.target, level)
        yield " in "
        yield from to_source(generator.iter, level)
        for if_node in generator.ifs:
            yield " if "
            yield from to_source(if_node, level)


def start_line(text: str, level: int) -> str:
    return f"{NEWLINE}{level * INDENTATION}{text}"


def with_separators(
    nodes: Sequence[ast.AST],
    level: int,
    include_final_separator: bool = False,
    separator: str = ", ",
) -> Iterator[str]:
    for node, comma in zip(
        nodes,
        separators(nodes, include_final_separator, separator),
        strict=True,
    ):
        yield from to_source(node, level)
        yield comma


def separators[T](
    sequence: Sequence[T],
    include_final_comma: bool = False,
    separator: str = ", ",
) -> tuple[str, ...]:
    if not sequence:
        return ()
    return (
        *repeat(separator, len(sequence) - 1),
        separator if include_final_comma else "",
    )
