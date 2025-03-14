import ast
import logging
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
    ast.Div: "/",
    ast.Mult: "*",
    ast.Sub: "-",
}
UNARY_OPERATORS = {ast.USub: "-", ast.Not: "not "}
BOOLEAN_OPERATORS = {ast.And: " and ", ast.Or: " or "}
FORMATTING_CONVERSIONS = {-1: "", 114: "!r"}


class NodeWithElse(Protocol):
    orelse: list[ast.stmt]


@singledispatch
def to_source(node: ast.AST, level: int = 0) -> Iterator[str]:
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
    yield "("
    yield from to_source(node.left, level)
    yield f" {BINARY_OPERATORS[type(node.op)]} "
    yield from to_source(node.right, level)
    yield ")"


@to_source.register
def unary_op(node: ast.UnaryOp, level: int) -> Iterator[str]:
    yield f" {UNARY_OPERATORS[type(node.op)]}"
    yield from to_source(node.operand, level)


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
    yield "("
    operators = (*repeat(BOOLEAN_OPERATORS[type(node.op)], len(node.values) - 1), "")
    for value, operator in zip(node.values, operators, strict=True):
        yield from to_source(value, level)
        yield operator
    yield ")"


@to_source.register
def subscript(node: ast.Subscript, level: int) -> Iterator[str]:
    yield from to_source(node.value, level)
    yield "["
    yield from to_source(node.slice, level)
    yield "]"


@to_source.register
def tuple_node(node: ast.Tuple, level: int) -> Iterator[str]:
    yield "("
    for element in node.elts:
        yield from to_source(element, level)
        yield ", "
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

    for argument, comma in zip(
        node.args,
        commas(node.args, include_final_comma=bool(node.keywords)),
        strict=True,
    ):
        yield from to_source(argument, level)
        yield comma
    for keyword, comma in zip(node.keywords, commas(node.keywords), strict=True):
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
    yield from to_source(node.left, level)
    for op, comparator in zip(node.ops, node.comparators, strict=False):
        yield f" {COMPARISONS[type(op)]} "
        yield from to_source(comparator, level)


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
    yield from render_else(node, level)


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
    print(ast.dump(node))
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

    if node.bases or node.keywords:
        yield "("
        for base in node.bases:
            yield from to_source(base, level)
            yield ", "
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
    for item, comma in zip(node.items, commas(node.items), strict=True):
        yield from to_source(item, level)
        yield comma

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
    for pattern in node.patterns:
        yield from to_source(pattern, level)
        yield ", "
    yield ")"


@to_source.register
def joined_str(node: ast.JoinedStr, level: int) -> Iterator[str]:
    strings = [part for value in node.values for part in to_source(value, level)]
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
    conversion = FORMATTING_CONVERSIONS[node.conversion]
    yield "".join(('f"{', *to_source(node.value, level), conversion, '}"'))


@to_source.register
def list_node(node: ast.List, level: int) -> Iterator[str]:
    yield "["
    for element in node.elts:
        yield from to_source(element, level)
        yield ", "
    yield "]"


@to_source.register
def set_node(node: ast.Set, level: int) -> Iterator[str]:
    yield "{"
    for element in node.elts:
        yield from to_source(element, level)
        yield ", "
    yield "}"


@to_source.register
def dict_node(node: ast.Dict, level: int) -> Iterator[str]:
    yield "{"
    for key, value in zip(node.keys, node.values, strict=True):
        if key and value:
            yield from to_source(key, level)
            yield ": "
            yield from to_source(value, level)
            yield ", "
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


def render_else(node: NodeWithElse, level: int) -> Iterator[str]:
    if not node.orelse:
        return
    yield start_line("else:", level)
    yield from render_body(node.orelse, level + 1)


def render_position_only_args(node: ast.arguments, level: int) -> Iterator[str]:
    if not node.posonlyargs:
        return
    for arg, comma in zip(
        node.posonlyargs,
        commas(
            node.posonlyargs,
            include_final_comma=bool(node.args or node.kwonlyargs),
        ),
        strict=True,
    ):
        yield from to_source(arg, level)
        yield comma
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
        commas(
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
    node: ast.FunctionDef | ast.AsyncFunctionDef, level: int
) -> Iterator[str]:
    if not node.type_params:
        return
    yield "["
    for type_parameter in node.type_params:
        yield from to_source(type_parameter, level)
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
        node.kwonlyargs, defaults, commas(node.kwonlyargs), strict=True
    ):
        yield from to_source(arg, level)
        if default:
            yield "="
            yield from to_source(default, level)
        yield comma


def render_annotation(node: ast.arg | ast.AnnAssign, level: int) -> Iterator[str]:
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


def render_decorators(decorators: Iterable[ast.AST], level: int) -> Iterator[str]:
    for decorator in decorators:
        yield start_line("@ ", level)
        yield from to_source(decorator, level)


def render_comprehension(
    node: ast.GeneratorExp | ast.ListComp | ast.SetComp, level: int
) -> Iterator[str]:
    yield from to_source(node.elt, level)
    for generator in node.generators:
        yield " for "
        yield from to_source(generator.target, level)
        yield " in "
        yield from to_source(generator.iter, level)
        for if_node in generator.ifs:
            yield " if "
            yield from to_source(if_node, level)


def start_line(text: str, level: int) -> str:
    return f"{NEWLINE}{level * INDENTATION}{text}"


def commas[T](
    sequence: Sequence[T], include_final_comma: bool = False
) -> tuple[str, ...]:
    if not sequence:
        return ()
    return (*repeat(", ", len(sequence) - 1), ", " if include_final_comma else "")
