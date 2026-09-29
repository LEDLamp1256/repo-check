"""Internal Python analysis: parse once with ``ast`` and extract normalized facts.

``analyze_python`` turns a file's decoded lines into ``PythonModuleFacts``, or
None when the source cannot be parsed by the running interpreter. Analyzed code
is never executed or imported; parser warnings are suppressed so analysis does
not write to stderr.

Facts are neutral: they record what the source contains (definition spans,
parameter defaults by AST node type, except handlers). Deciding what is a
problem is rule policy.

Qualified names follow Python's ``__qualname__`` convention: ``Outer.method``,
``func.<locals>.helper``.
"""

from __future__ import annotations

import ast
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum


class DefinitionKind(str, Enum):
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    CLASS = "class"


class ParameterKind(str, Enum):
    POSITIONAL = "positional"  # positional-only or positional-or-keyword
    KEYWORD_ONLY = "keyword_only"


@dataclass(frozen=True)
class PythonDefinition:
    """A ``def``, ``async def``, or ``class`` statement.

    ``start_line``/``end_line`` are the AST span (``lineno`` through
    ``end_lineno``, inclusive); ``start_line`` is the ``def``/``class`` line,
    not a decorator line.
    """

    kind: DefinitionKind
    name: str
    qualified_name: str
    start_line: int
    end_line: int

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass(frozen=True)
class PythonParameterDefault:
    """A parameter that has a default value, in a ``def`` or ``async def``."""

    function: str  # qualified name of the function
    parameter: str
    parameter_kind: ParameterKind
    default_node: str  # AST node class name of the default expression, e.g. "List"
    line: int  # line of the default expression


@dataclass(frozen=True)
class PythonExceptHandler:
    """An ``except`` clause of a ``try`` statement (not ``try``/``except*``)."""

    line: int
    bare: bool  # ``except:`` with no exception type
    enclosing: str | None  # qualified name of the enclosing definition, or None


@dataclass(frozen=True)
class PythonModuleFacts:
    definitions: tuple[PythonDefinition, ...] = ()
    parameter_defaults: tuple[PythonParameterDefault, ...] = ()
    except_handlers: tuple[PythonExceptHandler, ...] = ()


@dataclass(frozen=True)
class PythonAdapter:
    """Language adapter descriptor for Python (see ``repo_check.languages``)."""

    name: str = "python"
    extensions: frozenset[str] = field(default_factory=lambda: frozenset({".py", ".pyi"}))

    def analyze(self, lines: Sequence[str]) -> PythonModuleFacts | None:
        return analyze_python(lines)


PYTHON = PythonAdapter()


def analyze_python(lines: Sequence[str]) -> PythonModuleFacts | None:
    """Parse once and extract facts; None if the source does not parse."""
    tree = parse_module("\n".join(lines) + "\n")
    if tree is None:
        return None
    collector = _Collector()
    try:
        collector.visit_module(tree)
    except RecursionError:
        return None  # statement nesting beyond the interpreter's recursion limit
    return PythonModuleFacts(
        definitions=tuple(collector.definitions),
        parameter_defaults=tuple(collector.defaults),
        except_handlers=tuple(collector.handlers),
    )


def parse_module(source: str) -> ast.Module | None:
    """The single place Python source is parsed."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(source, type_comments=False)
    except (SyntaxError, ValueError, RecursionError):
        # ValueError: e.g. NUL bytes; RecursionError: pathologically nested code.
        return None


_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef

# Nodes whose list fields can hold statements: statements themselves, except
# handlers (``try``/``try*``), and ``match`` cases.
_STATEMENT_CONTAINERS = (ast.stmt, ast.excepthandler, ast.match_case)


class _Collector:
    """Walks statements only.

    Every fact comes from a statement (``def``, ``class``, ``try``), and
    expressions cannot contain statements (a lambda body is an expression), so
    expressions are never entered. This keeps the walk's depth bounded by
    statement nesting, which the parser limits, rather than by expression
    depth: a long ``a + b + ...`` chain parses into a very deep tree.
    """

    def __init__(self) -> None:
        self.definitions: list[PythonDefinition] = []
        self.defaults: list[PythonParameterDefault] = []
        self.handlers: list[PythonExceptHandler] = []
        # (qualified name, is_function) of enclosing definitions
        self._scope: list[tuple[str, bool]] = []

    def visit_module(self, tree: ast.Module) -> None:
        self._visit_body(tree.body)

    def _visit_body(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            self._visit_statement(statement)

    def _visit_statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.FunctionDef):
            self._enter(node, DefinitionKind.FUNCTION)
        elif isinstance(node, ast.AsyncFunctionDef):
            self._enter(node, DefinitionKind.ASYNC_FUNCTION)
        elif isinstance(node, ast.ClassDef):
            self._enter(node, DefinitionKind.CLASS)
        else:
            if isinstance(node, ast.Try):
                enclosing = self._scope[-1][0] if self._scope else None
                for handler in node.handlers:
                    self.handlers.append(
                        PythonExceptHandler(
                            line=handler.lineno, bare=handler.type is None, enclosing=enclosing
                        )
                    )
            self._visit_children(node)

    def _visit_children(self, node: ast.AST) -> None:
        # Field order is source order (body, handlers, orelse, finalbody, cases).
        for _, value in ast.iter_fields(node):
            if not isinstance(value, list):
                continue
            for child in value:
                if isinstance(child, ast.stmt):
                    self._visit_statement(child)
                elif isinstance(child, _STATEMENT_CONTAINERS):
                    self._visit_children(child)

    def _qualify(self, name: str) -> str:
        if not self._scope:
            return name
        parent, parent_is_function = self._scope[-1]
        return f"{parent}.<locals>.{name}" if parent_is_function else f"{parent}.{name}"

    def _enter(self, node: _FunctionNode | ast.ClassDef, kind: DefinitionKind) -> None:
        qualified = self._qualify(node.name)
        self.definitions.append(
            PythonDefinition(
                kind=kind,
                name=node.name,
                qualified_name=qualified,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
            )
        )
        if not isinstance(node, ast.ClassDef):
            self._record_defaults(node, qualified)
        self._scope.append((qualified, kind is not DefinitionKind.CLASS))
        self._visit_body(node.body)
        self._scope.pop()

    def _record_defaults(self, node: _FunctionNode, qualified: str) -> None:
        arguments = node.args
        positional = [*arguments.posonlyargs, *arguments.args]
        pairs: list[tuple[ast.arg, ast.expr, ParameterKind]] = [
            (arg, default, ParameterKind.POSITIONAL)
            for arg, default in zip(
                positional[len(positional) - len(arguments.defaults) :], arguments.defaults
            )
        ]
        pairs += [
            (arg, default, ParameterKind.KEYWORD_ONLY)
            for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults)
            if default is not None
        ]
        for arg, default, parameter_kind in pairs:
            self.defaults.append(
                PythonParameterDefault(
                    function=qualified,
                    parameter=arg.arg,
                    parameter_kind=parameter_kind,
                    default_node=type(default).__name__,
                    line=default.lineno,
                )
            )
