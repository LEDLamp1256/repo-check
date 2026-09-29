import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_check.config import parse_config
from repo_check.discovery import discover
from repo_check.engine import run_rules
from repo_check.languages import python as python_module
from repo_check.languages.facts import AnalysisFacts
from repo_check.languages.python import (
    DefinitionKind as K,
)
from repo_check.languages.python import (
    ParameterKind as P,
)
from repo_check.languages.python import (
    PythonDefinition,
    PythonExceptHandler,
    PythonParameterDefault,
    analyze_python,
)
from repo_check.rules import FILE_RULES

from support import write_tree


def analyze(source: str):
    return analyze_python(tuple(source.split("\n")))


class DefinitionFactsTests(unittest.TestCase):
    def test_functions_classes_methods_and_nesting(self):
        source = (
            "def top():\n"            # 1
            "    def inner():\n"      # 2
            "        pass\n"          # 3
            "    class Local:\n"      # 4
            "        def m(self): pass\n"  # 5
            "    return inner\n"      # 6
            "\n"
            "class Outer:\n"          # 8
            "    @staticmethod\n"     # 9
            "    def method():\n"     # 10
            "        return 1\n"      # 11
            "    async def amethod(self):\n"  # 12
            "        await x\n"       # 13
            "    class Inner:\n"      # 14
            "        pass\n"          # 15
            "\n"
            "async def fetch():\n"    # 17
            "    pass\n"              # 18
        )
        self.assertEqual(
            analyze(source).definitions,
            (
                PythonDefinition(K.FUNCTION, "top", "top", 1, 6),
                PythonDefinition(K.FUNCTION, "inner", "top.<locals>.inner", 2, 3),
                PythonDefinition(K.CLASS, "Local", "top.<locals>.Local", 4, 5),
                PythonDefinition(K.FUNCTION, "m", "top.<locals>.Local.m", 5, 5),
                PythonDefinition(K.CLASS, "Outer", "Outer", 8, 15),
                PythonDefinition(K.FUNCTION, "method", "Outer.method", 10, 11),
                PythonDefinition(K.ASYNC_FUNCTION, "amethod", "Outer.amethod", 12, 13),
                PythonDefinition(K.CLASS, "Inner", "Outer.Inner", 14, 15),
                PythonDefinition(K.ASYNC_FUNCTION, "fetch", "fetch", 17, 18),
            ),
        )

    def test_span_excludes_decorators_and_counts_inclusive_lines(self):
        source = "@decorator\n@other(1)\ndef f():\n    x = 1\n\n    return x\n"
        [definition] = analyze(source).definitions
        self.assertEqual((definition.start_line, definition.end_line), (3, 6))
        self.assertEqual(definition.line_count, 4)

    def test_definitions_inside_control_flow(self):
        source = "if X:\n    def f(): pass\nelse:\n    def f(): pass\ntry:\n    class C: pass\n" \
                 "except ImportError:\n    C = None\n"
        self.assertEqual(
            [(d.qualified_name, d.start_line) for d in analyze(source).definitions],
            [("f", 2), ("f", 4), ("C", 6)],
        )


class DefaultFactsTests(unittest.TestCase):
    def test_positional_and_keyword_only_alignment(self):
        source = (
            "def f(a, /, b=1, c=[], *args, d, e={}, f=None, **kw):\n"
            "    pass\n"
        )
        self.assertEqual(
            analyze(source).parameter_defaults,
            (
                PythonParameterDefault("f", "b", P.POSITIONAL, "Constant", 1),
                PythonParameterDefault("f", "c", P.POSITIONAL, "List", 1),
                PythonParameterDefault("f", "e", P.KEYWORD_ONLY, "Dict", 1),
                PythonParameterDefault("f", "f", P.KEYWORD_ONLY, "Constant", 1),
            ),
        )

    def test_positional_only_defaults(self):
        source = "def f(a=[], b={}, /, c=set()):\n    pass\n"
        self.assertEqual(
            [(d.parameter, d.parameter_kind, d.default_node)
             for d in analyze(source).parameter_defaults],
            [("a", P.POSITIONAL, "List"), ("b", P.POSITIONAL, "Dict"),
             ("c", P.POSITIONAL, "Call")],
        )

    def test_default_line_is_expression_line_and_methods_are_qualified(self):
        source = (
            "class C:\n"
            "    async def m(\n"
            "        self,\n"
            "        items=[\n"
            "            1,\n"
            "        ],\n"
            "    ):\n"
            "        pass\n"
        )
        self.assertEqual(
            analyze(source).parameter_defaults,
            (PythonParameterDefault("C.m", "items", P.POSITIONAL, "List", 4),),
        )

    def test_lambdas_are_not_functions(self):
        self.assertEqual(analyze("f = lambda x=[]: x\n").parameter_defaults, ())

    def test_nested_function_defaults_use_qualified_name(self):
        source = "def outer():\n    def inner(x={}):\n        pass\n"
        self.assertEqual(
            [d.function for d in analyze(source).parameter_defaults], ["outer.<locals>.inner"]
        )


class ExceptHandlerFactsTests(unittest.TestCase):
    def test_bare_and_typed_handlers(self):
        source = (
            "try:\n"                        # 1
            "    pass\n"
            "except:\n"                     # 3
            "    pass\n"
            "def f():\n"                    # 5
            "    try:\n"
            "        pass\n"
            "    except ValueError:\n"      # 8
            "        pass\n"
            "    except (A, B) as e:\n"     # 10
            "        pass\n"
            "    except:\n"                 # 12
            "        try:\n"
            "            pass\n"
            "        except Exception:\n"   # 15
            "            pass\n"
        )
        self.assertEqual(
            analyze(source).except_handlers,
            (
                PythonExceptHandler(3, True, None),
                PythonExceptHandler(8, False, "f"),
                PythonExceptHandler(10, False, "f"),
                PythonExceptHandler(12, True, "f"),
                PythonExceptHandler(15, False, "f"),
            ),
        )

    def test_except_star_is_not_recorded_but_nested_try_is(self):
        source = (
            "try:\n"
            "    pass\n"
            "except* ValueError:\n"
            "    try:\n"
            "        pass\n"
            "    except:\n"
            "        pass\n"
        )
        self.assertEqual(analyze(source).except_handlers, (PythonExceptHandler(6, True, None),))


class ParseFailureTests(unittest.TestCase):
    def test_syntax_error_returns_none(self):
        self.assertIsNone(analyze("def f(:\n    pass\n"))
        self.assertIsNone(analyze("print 'python 2'\n"))

    def test_nul_byte_returns_none(self):
        self.assertIsNone(analyze("x = 1\n\0\n"))

    def test_parser_warnings_are_not_emitted(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            facts = analyze('pattern = "\\d+"\nx = 1 is 1\n')
        self.assertIsNotNone(facts)
        self.assertEqual(stderr.getvalue(), "")

    def test_long_expression_chain_is_analyzed_not_a_crash(self):
        # 600 concatenated literals parse into an expression tree ~600 levels
        # deep; walking it recursively used to raise RecursionError.
        chain = " + ".join(['"a"'] * 600)
        facts = analyze(f"TABLE = {chain}\ndef f(x=[]):\n    try:\n        pass\n    except:\n"
                        f"        y = {chain}\n")
        self.assertEqual(
            [d.qualified_name for d in facts.definitions], ["f"]
        )
        self.assertEqual(facts.parameter_defaults[0].default_node, "List")
        self.assertEqual(facts.except_handlers, (PythonExceptHandler(5, True, "f"),))

    def test_statements_in_match_cases_and_handlers_are_found(self):
        source = (
            "match x:\n"                 # 1
            "    case 1:\n"
            "        def a(): pass\n"    # 3
            "    case _:\n"
            "        try:\n"
            "            pass\n"
            "        except:\n"          # 7
            "            class B: pass\n"  # 8
            "while y:\n"
            "    with z:\n"
            "        for i in y:\n"
            "            def c(): pass\n"  # 12
        )
        facts = analyze(source)
        self.assertEqual(
            [(d.qualified_name, d.start_line) for d in facts.definitions],
            [("a", 3), ("B", 8), ("c", 12)],
        )
        self.assertEqual(facts.except_handlers, (PythonExceptHandler(7, True, None),))

    def test_empty_module(self):
        facts = analyze("")
        self.assertEqual((facts.definitions, facts.parameter_defaults, facts.except_handlers),
                         ((), (), ()))


class ParseOnceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_python_file_parsed_once_across_all_rules(self):
        write_tree(
            self.root,
            {
                "a.py": "def f(x=[]):\n    try:\n        pass\n    except:\n        pass\n",
                "b.pyi": "def g(x: int = ...) -> None: ...\n",
                "tests/test_c.py": "class C:\n    pass\n",
                "broken.py": "def f(:\n",
                "README.md": "# not python\n",
                "build/gen.py": "def skipped(): pass\n",
            },
        )
        snapshot = discover(self.root, parse_config({}))
        with mock.patch.object(
            python_module, "parse_module", wraps=python_module.parse_module
        ) as parse:
            findings = run_rules(snapshot, FILE_RULES)
        parsed = sorted(call.args[0] for call in parse.call_args_list)
        self.assertEqual(len(parsed), 4)  # a.py, b.pyi, tests/test_c.py, broken.py
        self.assertEqual(
            sorted(f.rule_id for f in findings),
            ["PYTHON_BARE_EXCEPT", "PYTHON_MUTABLE_DEFAULT"],
        )

    def test_no_parse_when_python_rules_disabled(self):
        write_tree(self.root, {"a.py": "def f(x=[]): pass\n"})
        disabled = {
            rule: {"enabled": False}
            for rule in ("PYTHON_BARE_EXCEPT", "PYTHON_CLASS_TOO_LARGE",
                         "PYTHON_FUNCTION_TOO_LARGE", "PYTHON_MUTABLE_DEFAULT")
        }
        snapshot = discover(self.root, parse_config({"repo_check": {"rules": disabled}}))
        with mock.patch.object(
            python_module, "parse_module", wraps=python_module.parse_module
        ) as parse:
            run_rules(snapshot, FILE_RULES)
        self.assertEqual(parse.call_count, 0)

    def test_facts_cache_memoizes_per_file(self):
        write_tree(self.root, {"a.py": "x = 1\n", "b.txt": "not python\n"})
        snapshot = discover(self.root, parse_config({}))
        facts = AnalysisFacts()
        files = {f.relative_path: f for f in snapshot.files}
        with mock.patch.object(
            python_module, "parse_module", wraps=python_module.parse_module
        ) as parse:
            first = facts.python(files["a.py"])
            second = facts.python(files["a.py"])
            self.assertIsNone(facts.python(files["b.txt"]))
        self.assertIs(first, second)
        self.assertEqual(parse.call_count, 1)


if __name__ == "__main__":
    unittest.main()
