import tempfile
import unittest
from pathlib import Path

from repo_check.config import parse_config
from repo_check.discovery import discover
from repo_check.engine import run_rules
from repo_check.model import Finding, Location, Severity
from repo_check.rules import FILE_RULES
from repo_check.rules.python.bare_except import PythonBareExcept
from repo_check.rules.python.definition_too_large import (
    PythonClassTooLarge,
    PythonFunctionTooLarge,
)
from repo_check.rules.python.mutable_default import PythonMutableDefault

from support import write_tree


def function_body(total_lines: int, name: str = "f", indent: str = "") -> str:
    """A function whose def..last line span is exactly ``total_lines``."""
    body = "".join(f"{indent}    x{i} = {i}\n" for i in range(total_lines - 1))
    return f"{indent}def {name}():\n{body}"


def too_large(rule_id, noun, path, symbol, start, end, maximum):
    actual = end - start + 1
    return Finding(
        rule_id=rule_id,
        severity=Severity.WARNING,
        message=f"{noun} {symbol} spans {actual} lines; configured maximum is {maximum}.",
        path=path,
        location=Location(start_line=start, end_line=end, symbol=symbol),
        evidence={"actual_lines": actual, "maximum_lines": maximum, "symbol": symbol},
    )


def bare(path, line, symbol):
    return Finding(
        rule_id="PYTHON_BARE_EXCEPT",
        severity=Severity.WARNING,
        message="Bare 'except:' clause.",
        path=path,
        location=Location(start_line=line, symbol=symbol),
        evidence={"symbol": symbol},
    )


def mutable(path, line, symbol, parameter, kind, parameter_kind="positional"):
    return Finding(
        rule_id="PYTHON_MUTABLE_DEFAULT",
        severity=Severity.WARNING,
        message=(
            f"Parameter '{parameter}' of {symbol} has a mutable default "
            f"({kind.replace('_', ' ')})."
        ),
        path=path,
        location=Location(start_line=line, symbol=symbol),
        evidence={
            "default_kind": kind,
            "parameter": parameter,
            "parameter_kind": parameter_kind,
            "symbol": symbol,
        },
    )


class PythonRuleTestCase(unittest.TestCase):
    rules = ()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def findings(self, files, config=None, rules=None):
        root = Path(tempfile.mkdtemp(dir=self.base))
        write_tree(root, files)
        snapshot = discover(root, parse_config(config or {}))
        return list(run_rules(snapshot, rules if rules is not None else self.rules))


class MetadataTests(unittest.TestCase):
    def test_metadata_and_defaults(self):
        expected = {
            PythonFunctionTooLarge: "PYTHON_FUNCTION_TOO_LARGE",
            PythonClassTooLarge: "PYTHON_CLASS_TOO_LARGE",
            PythonBareExcept: "PYTHON_BARE_EXCEPT",
            PythonMutableDefault: "PYTHON_MUTABLE_DEFAULT",
        }
        for rule, rule_id in expected.items():
            with self.subTest(rule=rule_id):
                self.assertEqual(rule.metadata.id, rule_id)
                self.assertEqual(rule.metadata.default_severity, Severity.WARNING)
                self.assertTrue(rule.metadata.description and rule.metadata.remediation)
        config = parse_config({})
        self.assertEqual(dict(config.rule("PYTHON_FUNCTION_TOO_LARGE").options), {"max_lines": 100})
        self.assertEqual(dict(config.rule("PYTHON_CLASS_TOO_LARGE").options), {"max_lines": 500})
        self.assertEqual(dict(config.rule("PYTHON_BARE_EXCEPT").options), {})
        self.assertEqual(dict(config.rule("PYTHON_MUTABLE_DEFAULT").options), {})


class FunctionTooLargeTests(PythonRuleTestCase):
    rules = (PythonFunctionTooLarge(),)

    def test_default_boundary(self):
        self.assertEqual(self.findings({"a.py": function_body(100)}), [])
        self.assertEqual(
            self.findings({"a.py": function_body(101)}),
            [too_large("PYTHON_FUNCTION_TOO_LARGE", "Function", "a.py", "f", 1, 101, 100)],
        )

    def test_configured_boundary(self):
        config = {"repo_check": {"rules": {"PYTHON_FUNCTION_TOO_LARGE": {"max_lines": 3}}}}
        self.assertEqual(self.findings({"a.py": function_body(3)}, config), [])
        self.assertEqual(
            self.findings({"a.py": function_body(4)}, config),
            [too_large("PYTHON_FUNCTION_TOO_LARGE", "Function", "a.py", "f", 1, 4, 3)],
        )

    def test_async_methods_nested_and_decorated(self):
        config = {"repo_check": {"rules": {"PYTHON_FUNCTION_TOO_LARGE": {"max_lines": 2}}}}
        source = (
            "class Service:\n"               # 1
            "    @property\n"                # 2
            "    async def load(self):\n"    # 3
            "        a = 1\n"                # 4
            "        return a\n"             # 5
            "    def small(self): pass\n"    # 6
            "def outer():\n"                 # 7
            "    def inner():\n"             # 8
            "        a = 1\n"                # 9
            "        return a\n"             # 10
            "    return inner\n"             # 11
        )
        self.assertEqual(
            self.findings({"src/svc.py": source}, config),
            [
                too_large("PYTHON_FUNCTION_TOO_LARGE", "Function", "src/svc.py",
                          "Service.load", 3, 5, 2),
                too_large("PYTHON_FUNCTION_TOO_LARGE", "Function", "src/svc.py",
                          "outer", 7, 11, 2),
                too_large("PYTHON_FUNCTION_TOO_LARGE", "Function", "src/svc.py",
                          "outer.<locals>.inner", 8, 10, 2),
            ],
        )

    def test_classes_are_not_functions(self):
        config = {"repo_check": {"rules": {"PYTHON_FUNCTION_TOO_LARGE": {"max_lines": 1}}}}
        self.assertEqual(self.findings({"a.py": "class C:\n    x = 1\n"}, config), [])


class ClassTooLargeTests(PythonRuleTestCase):
    rules = (PythonClassTooLarge(),)

    def class_source(self, total_lines, name="C"):
        return f"class {name}:\n" + "".join(f"    x{i} = {i}\n" for i in range(total_lines - 1))

    def test_default_boundary(self):
        self.assertEqual(self.findings({"a.py": self.class_source(500)}), [])
        self.assertEqual(
            self.findings({"a.py": self.class_source(501)}),
            [too_large("PYTHON_CLASS_TOO_LARGE", "Class", "a.py", "C", 1, 501, 500)],
        )

    def test_nested_classes_and_functions_are_not_classes(self):
        config = {"repo_check": {"rules": {"PYTHON_CLASS_TOO_LARGE": {"max_lines": 2}}}}
        source = (
            "class Outer:\n"          # 1
            "    class Inner:\n"      # 2
            "        a = 1\n"         # 3
            "        b = 2\n"         # 4
            "def f():\n"              # 5
            "    class Local:\n"      # 6
            "        a = 1\n"         # 7
            "        b = 2\n"         # 8
            "    return Local\n"      # 9
        )
        self.assertEqual(
            self.findings({"a.py": source}, config),
            [
                too_large("PYTHON_CLASS_TOO_LARGE", "Class", "a.py", "Outer", 1, 4, 2),
                too_large("PYTHON_CLASS_TOO_LARGE", "Class", "a.py", "Outer.Inner", 2, 4, 2),
                too_large("PYTHON_CLASS_TOO_LARGE", "Class", "a.py", "f.<locals>.Local", 6, 8, 2),
            ],
        )


class BareExceptTests(PythonRuleTestCase):
    rules = (PythonBareExcept(),)

    def test_positive_and_typed_negatives(self):
        source = (
            "try:\n"                              # 1
            "    pass\n"
            "except:\n"                           # 3
            "    pass\n"
            "try:\n"
            "    pass\n"
            "except Exception:\n"
            "    pass\n"
            "except (ValueError, TypeError):\n"
            "    pass\n"
            "except BaseException as exc:\n"
            "    pass\n"
            "class C:\n"                          # 13
            "    def m(self):\n"                  # 14
            "        try:\n"
            "            pass\n"
            "        except OSError:\n"
            "            pass\n"
            "        except:\n"                   # 19
            "            raise\n"
        )
        self.assertEqual(
            self.findings({"a.py": source}), [bare("a.py", 3, None), bare("a.py", 19, "C.m")]
        )

    def test_except_star_is_not_reported(self):
        source = "try:\n    pass\nexcept* ValueError:\n    pass\nexcept* (A, B):\n    pass\n"
        self.assertEqual(self.findings({"a.py": source}), [])

    def test_text_mentions_are_not_reported(self):
        source = '"""\ntry:\n    x\nexcept:\n    y\n"""\n# except:\ns = "except:"\n'
        self.assertEqual(self.findings({"a.py": source}), [])


class MutableDefaultTests(PythonRuleTestCase):
    rules = (PythonMutableDefault(),)

    def test_all_six_mutable_forms(self):
        source = (
            "def f(\n"                                  # 1
            "    a=[],\n"                               # 2
            "    b={},\n"                               # 3
            "    c={1, 2},\n"                           # 4
            "    d=[i for i in range(3)],\n"            # 5
            "    e={k: k for k in 'ab'},\n"             # 6
            "    g={i for i in range(3)},\n"            # 7
            "):\n"
            "    pass\n"
        )
        self.assertEqual(
            self.findings({"a.py": source}),
            [
                mutable("a.py", 2, "f", "a", "list"),
                mutable("a.py", 3, "f", "b", "dict"),
                mutable("a.py", 4, "f", "c", "set"),
                mutable("a.py", 5, "f", "d", "list_comprehension"),
                mutable("a.py", 6, "f", "e", "dict_comprehension"),
                mutable("a.py", 7, "f", "g", "set_comprehension"),
            ],
        )

    def test_positional_and_keyword_only(self):
        source = "class C:\n    async def m(self, a=[], /, b={}, *, c=[], d=None):\n        pass\n"
        self.assertEqual(
            self.findings({"a.py": source}),
            [
                mutable("a.py", 2, "C.m", "a", "list"),
                mutable("a.py", 2, "C.m", "b", "dict"),
                mutable("a.py", 2, "C.m", "c", "list", "keyword_only"),
            ],
        )

    def test_immutable_and_call_defaults_are_not_reported(self):
        source = (
            "import collections\n"
            "def f(a=None, b=0, c='s', d=(), e=(1, [2]), f=frozenset(), g=list(), h=dict(),\n"
            "      i=set(), j=collections.defaultdict(list), k=MyList(), l=make(), m=...,\n"
            "      n=-1, o=b'x', p=lambda: [], q=[1][0], r={}.get('k'), s=x if y else [],\n"
            "      *, t=list(), u=tuple([])):\n"
            "    pass\n"
            "g = lambda x=[]: x\n"
        )
        self.assertEqual(self.findings({"a.py": source}), [])

    def test_nested_function_symbol(self):
        source = "def outer():\n    def inner(cache={}):\n        return cache\n"
        self.assertEqual(
            self.findings({"a.py": source}),
            [mutable("a.py", 2, "outer.<locals>.inner", "cache", "dict")],
        )


class ScopeTests(PythonRuleTestCase):
    rules = (PythonBareExcept(),)
    source = "try:\n    pass\nexcept:\n    pass\n"

    def test_source_test_and_stub_files(self):
        files = {
            "src/app.py": self.source,
            "tests/test_app.py": self.source,
            "stubs/app.pyi": "def f(x: list = []) -> None: ...\n",
            "tests/conftest.py": self.source,
        }
        self.assertEqual(
            [(f.rule_id, f.path) for f in self.findings(files, rules=FILE_RULES)],
            [
                ("PYTHON_BARE_EXCEPT", "src/app.py"),
                ("PYTHON_MUTABLE_DEFAULT", "stubs/app.pyi"),
                ("PYTHON_BARE_EXCEPT", "tests/conftest.py"),
                ("PYTHON_BARE_EXCEPT", "tests/test_app.py"),
            ],
        )

    def test_non_eligible_files_are_skipped(self):
        files = {
            "build/lib/app.py": self.source,
            "gen/api.py": "# @generated\n" + self.source,
            "notes.txt": self.source,
            "script": "#!/usr/bin/env python\n" + self.source,
        }
        self.assertEqual(self.findings(files), [])

    def test_syntax_error_file_is_skipped_without_crashing(self):
        files = {
            "broken.py": "def f(:\n    try:\n        pass\n    except:\n        pass\n",
            "ok.py": self.source,
            "big.py": "# TODO: still checked\n" + "x = 1\n" * 1000 + "def (:\n",
        }
        self.assertEqual(
            [(f.rule_id, f.path) for f in self.findings(files, rules=FILE_RULES)],
            [
                ("FILE_TOO_LARGE", "big.py"),
                ("TODO_COMMENT", "big.py"),
                ("PYTHON_BARE_EXCEPT", "ok.py"),
            ],
        )

    def test_rule_disabling(self):
        for rule_id in ("PYTHON_BARE_EXCEPT", "PYTHON_MUTABLE_DEFAULT",
                        "PYTHON_FUNCTION_TOO_LARGE", "PYTHON_CLASS_TOO_LARGE"):
            with self.subTest(rule=rule_id):
                config = {"repo_check": {"rules": {
                    rule_id: {"enabled": False},
                    "PYTHON_FUNCTION_TOO_LARGE": {"enabled": rule_id != "PYTHON_FUNCTION_TOO_LARGE",
                                                  "max_lines": 1},
                    "PYTHON_CLASS_TOO_LARGE": {"enabled": rule_id != "PYTHON_CLASS_TOO_LARGE",
                                               "max_lines": 1},
                }}}
                source = "class C:\n    def f(self, x=[]):\n        try:\n            pass\n" \
                         "        except:\n            pass\n"
                found = {f.rule_id for f in self.findings({"a.py": source}, config, FILE_RULES)}
                self.assertNotIn(rule_id, found)
                self.assertEqual(len(found), 3)

    def test_deterministic_mixed_ordering(self):
        files = {
            "b.py": "def f(x=[]):\n    pass\n",
            "a/z.py": "try:\n    pass\nexcept:\n    pass\n",
            "a.py": "def g(y={}, *, z=[]):\n    pass\ntry:\n    pass\nexcept:\n    pass\n",
        }
        first = self.findings(files, rules=FILE_RULES)
        self.assertEqual(
            [(f.path, f.location.start_line, f.rule_id) for f in first],
            [
                ("a.py", 1, "PYTHON_MUTABLE_DEFAULT"),
                ("a.py", 1, "PYTHON_MUTABLE_DEFAULT"),
                ("a.py", 5, "PYTHON_BARE_EXCEPT"),
                ("a/z.py", 3, "PYTHON_BARE_EXCEPT"),
                ("b.py", 1, "PYTHON_MUTABLE_DEFAULT"),
            ],
        )
        self.assertEqual([f.evidence["parameter"] for f in first[:2]], ["y", "z"])
        self.assertEqual(first, self.findings(files, rules=FILE_RULES))


class ConfigTests(unittest.TestCase):
    def test_invalid_threshold_values(self):
        from repo_check.errors import ConfigError

        for rule_id in ("PYTHON_FUNCTION_TOO_LARGE", "PYTHON_CLASS_TOO_LARGE"):
            for value in (0, -1, "100", 1.5, True):
                with self.subTest(rule=rule_id, value=value), self.assertRaisesRegex(
                    ConfigError, rf"{rule_id}\.max_lines must be a positive integer"
                ):
                    parse_config({"repo_check": {"rules": {rule_id: {"max_lines": value}}}})

    def test_optionless_rules_reject_options(self):
        from repo_check.errors import ConfigError

        for rule_id in ("PYTHON_BARE_EXCEPT", "PYTHON_MUTABLE_DEFAULT"):
            with self.subTest(rule=rule_id), self.assertRaisesRegex(ConfigError, "max_lines"):
                parse_config({"repo_check": {"rules": {rule_id: {"max_lines": 5}}}})

    def test_valid_threshold_override(self):
        config = parse_config(
            {"repo_check": {"rules": {"PYTHON_CLASS_TOO_LARGE": {"max_lines": 42}}}}
        )
        self.assertEqual(config.rule("PYTHON_CLASS_TOO_LARGE").options["max_lines"], 42)
        self.assertTrue(config.rule("PYTHON_CLASS_TOO_LARGE").enabled)


if __name__ == "__main__":
    unittest.main()
