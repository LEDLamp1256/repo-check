import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_check import cli

from support import (
    FIXTURES,
    commit_all,
    git,
    make_branch_repo,
    make_git_repo,
    numbered_lines,
    requires_git,
    run_cli,
    write_tree,
)

OVERSIZED_TEXT = (
    "src/over.py: warning FILE_TOO_LARGE: File contains 6 lines; configured maximum is 5.\n"
    "  evidence: actual_lines=6, maximum_lines=5\n"
    "tests/test_over.py: warning FILE_TOO_LARGE: "
    "File contains 7 lines; configured maximum is 5.\n"
    "  evidence: actual_lines=7, maximum_lines=5\n"
    "\n"
    "2 findings (0 error, 2 warning, 0 info).\n"
)

OVERSIZED_JSON = {
    "schema_version": 1,
    "findings": [
        {
            "rule_id": "FILE_TOO_LARGE",
            "severity": "warning",
            "path": "src/over.py",
            "location": None,
            "message": "File contains 6 lines; configured maximum is 5.",
            "evidence": {"actual_lines": 6, "maximum_lines": 5},
        },
        {
            "rule_id": "FILE_TOO_LARGE",
            "severity": "warning",
            "path": "tests/test_over.py",
            "location": None,
            "message": "File contains 7 lines; configured maximum is 5.",
            "evidence": {"actual_lines": 7, "maximum_lines": 5},
        },
    ],
}


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_repository_text(self):
        result = run_cli(str(FIXTURES / "clean_repository"))
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, "No findings.\n", "")
        )

    def test_clean_repository_json(self):
        result = run_cli(str(FIXTURES / "clean_repository"), "--format", "json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '{\n  "schema_version": 1,\n  "findings": []\n}\n')

    def test_findings_text_default_fail_on_is_error(self):
        result = run_cli(str(FIXTURES / "oversized_file"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, OVERSIZED_TEXT)
        self.assertEqual(result.stderr, "")

    def test_findings_json(self):
        result = run_cli(str(FIXTURES / "oversized_file"), "--format", "json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), OVERSIZED_JSON)
        second = run_cli(str(FIXTURES / "oversized_file"), "--format", "json")
        self.assertEqual(result.stdout, second.stdout)

    def test_output_has_no_absolute_paths(self):
        result = run_cli(str(FIXTURES / "oversized_file"), "--format", "json")
        self.assertNotIn(str(FIXTURES), result.stdout)

    def test_relative_path_argument(self):
        result = run_cli("oversized_file", "--format", "json", cwd=FIXTURES)
        self.assertEqual(json.loads(result.stdout), OVERSIZED_JSON)

    def test_fail_on_thresholds(self):
        path = str(FIXTURES / "oversized_file")
        for level, code in (("info", 1), ("warning", 1), ("error", 0)):
            with self.subTest(level=level):
                result = run_cli(path, "--fail-on", level)
                self.assertEqual(result.returncode, code)
                self.assertEqual(result.stdout, OVERSIZED_TEXT)

    def test_fail_on_with_no_findings(self):
        result = run_cli(str(FIXTURES / "clean_repository"), "--fail-on", "info")
        self.assertEqual(result.returncode, 0)

    def test_explicit_config(self):
        write_tree(self.tmp, {"src/a.py": numbered_lines(3)})
        config = self.tmp / "custom.toml"
        config.write_text("[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = 2\n", encoding="utf-8")
        result = run_cli(str(self.tmp / "src"), "--config", str(config), "--fail-on", "warning")
        self.assertEqual(result.returncode, 1)
        self.assertIn("a.py: warning FILE_TOO_LARGE: File contains 3 lines;", result.stdout)

    def test_explicit_config_overrides_repository_config(self):
        config = self.tmp / "off.toml"
        config.write_text("[repo_check.rules.FILE_TOO_LARGE]\nenabled = false\n", encoding="utf-8")
        result = run_cli(str(FIXTURES / "oversized_file"), "--config", str(config))
        self.assertEqual((result.returncode, result.stdout), (0, "No findings.\n"))

    def test_malformed_config_exits_2(self):
        write_tree(self.tmp, {"repo-check.toml": "[repo_check\n", "a.py": ""})
        result = run_cli(str(self.tmp))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("repo-check: error:", result.stderr)
        self.assertIn("malformed TOML", result.stderr)

    def test_invalid_setting_exits_2(self):
        write_tree(
            self.tmp,
            {"repo-check.toml": '[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = "big"\n'},
        )
        result = run_cli(str(self.tmp), "--format", "json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("max_lines must be a positive integer", result.stderr)

    def test_missing_explicit_config_exits_2(self):
        result = run_cli(str(self.tmp), "--config", str(self.tmp / "nope.toml"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("config file not found", result.stderr)

    def test_missing_path_exits_2(self):
        result = run_cli(str(self.tmp / "missing"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("path does not exist", result.stderr)

    def test_usage_errors_exit_2(self):
        for args in ((), (str(self.tmp), "--format", "xml"), (str(self.tmp), "--fail-on", "fatal"),
                     (str(self.tmp), "--compare", "dev")):
            with self.subTest(args=args):
                self.assertEqual(run_cli(*args).returncode, 2)


TRACKED_TEXT = (
    "build/output.o: warning TRACKED_BUILD_ARTIFACT: Build artifact is tracked by Git.\n"
    '  evidence: classification="build_artifact", tracked=true\n'
    "dist/app bundle.js: warning TRACKED_BUILD_ARTIFACT: Build artifact is tracked by Git.\n"
    '  evidence: classification="build_artifact", tracked=true\n'
    "src/big.py: warning FILE_TOO_LARGE: File contains 6 lines; configured maximum is 5.\n"
    "  evidence: actual_lines=6, maximum_lines=5\n"
    "\n"
    "3 findings (0 error, 3 warning, 0 info).\n"
)

TRACKED_JSON = (
    '{\n'
    '  "schema_version": 1,\n'
    '  "findings": [\n'
    '    {\n'
    '      "rule_id": "TRACKED_BUILD_ARTIFACT",\n'
    '      "severity": "warning",\n'
    '      "path": "build/output.o",\n'
    '      "location": null,\n'
    '      "message": "Build artifact is tracked by Git.",\n'
    '      "evidence": {\n'
    '        "classification": "build_artifact",\n'
    '        "tracked": true\n'
    '      }\n'
    '    },\n'
    '    {\n'
    '      "rule_id": "TRACKED_BUILD_ARTIFACT",\n'
    '      "severity": "warning",\n'
    '      "path": "dist/app bundle.js",\n'
    '      "location": null,\n'
    '      "message": "Build artifact is tracked by Git.",\n'
    '      "evidence": {\n'
    '        "classification": "build_artifact",\n'
    '        "tracked": true\n'
    '      }\n'
    '    },\n'
    '    {\n'
    '      "rule_id": "FILE_TOO_LARGE",\n'
    '      "severity": "warning",\n'
    '      "path": "src/big.py",\n'
    '      "location": null,\n'
    '      "message": "File contains 6 lines; configured maximum is 5.",\n'
    '      "evidence": {\n'
    '        "actual_lines": 6,\n'
    '        "maximum_lines": 5\n'
    '      }\n'
    '    }\n'
    '  ]\n'
    '}\n'
)


@requires_git
class TrackedArtifactCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_git_repo(
            Path(self._tmp.name) / "repo",
            tracked={
                "build/output.o": b"\0",
                "dist/app bundle.js": "x;\n",
                "src/big.py": numbered_lines(6),
                "src/ok.py": numbered_lines(2),
                "repo-check.toml": "[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = 5\n",
            },
            untracked={"build/untracked.o": b"\0"},
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_text(self):
        result = run_cli(".", cwd=self.root)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, TRACKED_TEXT, ""))

    def test_json_exact_and_byte_identical(self):
        first = run_cli(".", "--format", "json", cwd=self.root)
        second = run_cli(".", "--format", "json", cwd=self.root)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, TRACKED_JSON)
        self.assertEqual(first.stdout.encode(), second.stdout.encode())
        self.assertNotIn(str(self.root), first.stdout)

    def test_fail_on_thresholds(self):
        for level, code in (("info", 1), ("warning", 1), ("error", 0)):
            with self.subTest(level=level):
                self.assertEqual(run_cli(str(self.root), "--fail-on", level).returncode, code)

    def test_rule_disabled_by_explicit_config(self):
        config = Path(self._tmp.name) / "off.toml"
        config.write_text(
            "[repo_check.rules.TRACKED_BUILD_ARTIFACT]\nenabled = false\n", encoding="utf-8"
        )
        result = run_cli(str(self.root), "--config", str(config), "--fail-on", "warning")
        self.assertEqual((result.returncode, result.stdout), (0, "No findings.\n"))

    def test_subdirectory_analysis(self):
        repo = make_git_repo(
            Path(self._tmp.name) / "mono",
            tracked={"pkg/build/lib.o": b"\0", "pkg/src/a.py": "", "other/build/x.o": b"\0"},
        )
        result = run_cli(str(repo / "pkg"), "--format", "json")
        paths = [f["path"] for f in json.loads(result.stdout)["findings"]]
        self.assertEqual(paths, ["build/lib.o"])

    def test_without_git_executable(self):
        empty_bin = Path(self._tmp.name) / "empty-bin"
        empty_bin.mkdir()
        result = run_cli(str(self.root), env_overrides={"PATH": str(empty_bin)})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout,
            "src/big.py: warning FILE_TOO_LARGE: File contains 6 lines; configured maximum is 5.\n"
            "  evidence: actual_lines=6, maximum_lines=5\n"
            "\n"
            "1 finding (0 error, 1 warning, 0 info).\n",
        )


RC1B_TEXT = (
    "README.md:3: warning BROKEN_LOCAL_DOC_LINK: Local link target does not exist: "
    "docs/missing.md\n"
    '  evidence: link="docs/missing.md", resolved_path="docs/missing.md"\n'
    "src/app.py:2: info TODO_COMMENT: Comment contains a TODO marker.\n"
    '  evidence: marker="TODO", text="TODO: handle errors"\n'
    "src/app.py:4: info TODO_COMMENT: Comment contains a FIXME marker.\n"
    '  evidence: marker="FIXME", text="FIXME(alice): off by one"\n'
    "\n"
    "3 findings (0 error, 1 warning, 2 info).\n"
)


class DocLinkAndTodoCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        write_tree(
            self.root,
            {
                "README.md": "# Project\n\n[Guide](docs/guide.md), [Missing](docs/missing.md)\n",
                "docs/guide.md": "[Home](../README.md)\n",
                "src/app.py": (
                    "import os\n# TODO: handle errors\nx = 1\ny = 2  # FIXME(alice): off by one\n"
                ),
                # BROKEN_LOCAL_DOC_LINK is disabled by default.
                "repo-check.toml": "[repo_check.rules.BROKEN_LOCAL_DOC_LINK]\nenabled = true\n",
            },
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_text(self):
        result = run_cli(".", cwd=self.root)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, RC1B_TEXT, ""))

    def test_json_exact_and_byte_identical(self):
        first = run_cli(str(self.root), "--format", "json")
        second = run_cli(str(self.root), "--format", "json")
        self.assertEqual(first.stdout.encode(), second.stdout.encode())
        document = json.loads(first.stdout)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(
            document["findings"],
            [
                {
                    "rule_id": "BROKEN_LOCAL_DOC_LINK",
                    "severity": "warning",
                    "path": "README.md",
                    "location": {"start_line": 3, "end_line": None, "symbol": None},
                    "message": "Local link target does not exist: docs/missing.md",
                    "evidence": {"link": "docs/missing.md", "resolved_path": "docs/missing.md"},
                },
                {
                    "rule_id": "TODO_COMMENT",
                    "severity": "info",
                    "path": "src/app.py",
                    "location": {"start_line": 2, "end_line": None, "symbol": None},
                    "message": "Comment contains a TODO marker.",
                    "evidence": {"marker": "TODO", "text": "TODO: handle errors"},
                },
                {
                    "rule_id": "TODO_COMMENT",
                    "severity": "info",
                    "path": "src/app.py",
                    "location": {"start_line": 4, "end_line": None, "symbol": None},
                    "message": "Comment contains a FIXME marker.",
                    "evidence": {"marker": "FIXME", "text": "FIXME(alice): off by one"},
                },
            ],
        )

    def test_fail_on_thresholds(self):
        for level, code in (("info", 1), ("warning", 1), ("error", 0)):
            with self.subTest(level=level):
                self.assertEqual(run_cli(str(self.root), "--fail-on", level).returncode, code)

    def test_link_rule_is_disabled_by_default(self):
        (self.root / "repo-check.toml").unlink()
        result = run_cli(".", cwd=self.root)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("BROKEN_LOCAL_DOC_LINK", result.stdout)
        self.assertTrue(result.stdout.endswith("2 findings (0 error, 0 warning, 2 info).\n"))
        self.assertEqual(run_cli(str(self.root), "--fail-on", "warning").returncode, 0)

    def test_info_only_findings_fail_only_on_info(self):
        (self.root / "README.md").write_text("# Project\n", encoding="utf-8")
        self.assertEqual(run_cli(str(self.root), "--fail-on", "warning").returncode, 0)
        self.assertEqual(run_cli(str(self.root), "--fail-on", "info").returncode, 1)

    def test_rules_disabled_by_config(self):
        (self.root / "repo-check.toml").write_text(
            "[repo_check.rules.TODO_COMMENT]\nenabled = false\n\n"
            "[repo_check.rules.BROKEN_LOCAL_DOC_LINK]\nenabled = false\n",
            encoding="utf-8",
        )
        result = run_cli(str(self.root), "--fail-on", "info")
        self.assertEqual((result.returncode, result.stdout), (0, "No findings.\n"))

    def test_unknown_option_for_optionless_rule_exits_2(self):
        (self.root / "repo-check.toml").write_text(
            '[repo_check.rules.TODO_COMMENT]\nmarkers = ["XXX"]\n', encoding="utf-8"
        )
        result = run_cli(str(self.root))
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown key(s) in repo_check.rules.TODO_COMMENT: markers", result.stderr)


RC2_SOURCE = (
    "# TODO: split module\n"                  # 1
    "class Store:\n"                          # 2
    "    def load(self, keys=[], *, cache={}):\n"  # 3
    "        try:\n"                          # 4
    "            return keys\n"               # 5
    "        except:\n"                       # 6
    "            return None\n"               # 7
)

RC2_TEXT = (
    "src/store.py:1: info TODO_COMMENT: Comment contains a TODO marker.\n"
    '  evidence: marker="TODO", text="TODO: split module"\n'
    "src/store.py:2-7 (Store): warning PYTHON_CLASS_TOO_LARGE: "
    "Class Store spans 6 lines; configured maximum is 5.\n"
    '  evidence: actual_lines=6, maximum_lines=5, symbol="Store"\n'
    "src/store.py:3-7 (Store.load): warning PYTHON_FUNCTION_TOO_LARGE: "
    "Function Store.load spans 5 lines; configured maximum is 4.\n"
    '  evidence: actual_lines=5, maximum_lines=4, symbol="Store.load"\n'
    "src/store.py:3 (Store.load): warning PYTHON_MUTABLE_DEFAULT: "
    "Parameter 'cache' of Store.load has a mutable default (dict).\n"
    '  evidence: default_kind="dict", parameter="cache", parameter_kind="keyword_only", '
    'symbol="Store.load"\n'
    "src/store.py:3 (Store.load): warning PYTHON_MUTABLE_DEFAULT: "
    "Parameter 'keys' of Store.load has a mutable default (list).\n"
    '  evidence: default_kind="list", parameter="keys", parameter_kind="positional", '
    'symbol="Store.load"\n'
    "src/store.py:6 (Store.load): warning PYTHON_BARE_EXCEPT: Bare 'except:' clause.\n"
    '  evidence: symbol="Store.load"\n'
    "\n"
    "6 findings (0 error, 5 warning, 1 info).\n"
)


class PythonRulesCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        write_tree(
            self.root,
            {
                "src/store.py": RC2_SOURCE,
                "src/broken.py": "def f(:\n",
                "repo-check.toml": (
                    "[repo_check.rules.PYTHON_FUNCTION_TOO_LARGE]\nmax_lines = 4\n\n"
                    "[repo_check.rules.PYTHON_CLASS_TOO_LARGE]\nmax_lines = 5\n"
                ),
            },
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_text(self):
        result = run_cli(".", cwd=self.root)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, RC2_TEXT, ""))

    def test_json_exact_and_byte_identical(self):
        first = run_cli(str(self.root), "--format", "json")
        second = run_cli(str(self.root), "--format", "json")
        self.assertEqual(first.stdout.encode(), second.stdout.encode())
        findings = json.loads(first.stdout)["findings"]
        self.assertEqual(
            [(f["rule_id"], f["location"]) for f in findings],
            [
                ("TODO_COMMENT", {"start_line": 1, "end_line": None, "symbol": None}),
                ("PYTHON_CLASS_TOO_LARGE", {"start_line": 2, "end_line": 7, "symbol": "Store"}),
                ("PYTHON_FUNCTION_TOO_LARGE",
                 {"start_line": 3, "end_line": 7, "symbol": "Store.load"}),
                ("PYTHON_MUTABLE_DEFAULT",
                 {"start_line": 3, "end_line": None, "symbol": "Store.load"}),
                ("PYTHON_MUTABLE_DEFAULT",
                 {"start_line": 3, "end_line": None, "symbol": "Store.load"}),
                ("PYTHON_BARE_EXCEPT",
                 {"start_line": 6, "end_line": None, "symbol": "Store.load"}),
            ],
        )
        self.assertEqual(
            findings[2],
            {
                "rule_id": "PYTHON_FUNCTION_TOO_LARGE",
                "severity": "warning",
                "path": "src/store.py",
                "location": {"start_line": 3, "end_line": 7, "symbol": "Store.load"},
                "message": "Function Store.load spans 5 lines; configured maximum is 4.",
                "evidence": {"actual_lines": 5, "maximum_lines": 4, "symbol": "Store.load"},
            },
        )

    def test_fail_on_thresholds(self):
        for level, code in (("info", 1), ("warning", 1), ("error", 0)):
            with self.subTest(level=level):
                self.assertEqual(run_cli(str(self.root), "--fail-on", level).returncode, code)

    def test_invalid_threshold_exits_2(self):
        (self.root / "repo-check.toml").write_text(
            "[repo_check.rules.PYTHON_FUNCTION_TOO_LARGE]\nmax_lines = 0\n", encoding="utf-8"
        )
        result = run_cli(str(self.root))
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("PYTHON_FUNCTION_TOO_LARGE.max_lines must be a positive integer",
                      result.stderr)

    def test_option_on_optionless_python_rule_exits_2(self):
        (self.root / "repo-check.toml").write_text(
            "[repo_check.rules.PYTHON_BARE_EXCEPT]\nmax_lines = 3\n", encoding="utf-8"
        )
        result = run_cli(str(self.root))
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown key(s) in repo_check.rules.PYTHON_BARE_EXCEPT", result.stderr)


RC3_TEXT = (
    "Sources/App/Loader.swift:3: warning SWIFT_FORCE_TRY: Force-try operator 'try!' is used.\n"
    '  evidence: operator="try!"\n'
    "Sources/App/Loader.swift:4: info TODO_COMMENT: Comment contains a TODO marker.\n"
    '  evidence: marker="TODO", text="TODO: avoid force cast"\n'
    "Sources/App/Loader.swift:5: warning SWIFT_FORCE_CAST: "
    "Forced cast operator 'as!' is used.\n"
    '  evidence: operator="as!"\n'
    "Sources/App/Loader.swift:5: warning SWIFT_FORCE_TRY: Force-try operator 'try!' is used.\n"
    '  evidence: operator="try!"\n'
    "tools/gen.py:1 (make): warning PYTHON_MUTABLE_DEFAULT: "
    "Parameter 'items' of make has a mutable default (list).\n"
    '  evidence: default_kind="list", parameter="items", parameter_kind="positional", '
    'symbol="make"\n'
    "\n"
    "5 findings (0 error, 4 warning, 1 info).\n"
)


class SwiftRulesCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        write_tree(
            self.root,
            {
                "Sources/App/Loader.swift": (
                    "import Foundation\n"
                    'let label = "try! as! in a string" // try! as! in a comment\n'
                    "let data = try! Data(contentsOf: url)\n"
                    "// TODO: avoid force cast\n"
                    "let json = try! decode(data) as! [String: Any]\n"
                    "let safe = try? decode(data) as? Int\n"
                ),
                "tools/gen.py": "def make(items=[]):\n    return items\n",
            },
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_text(self):
        result = run_cli(".", cwd=self.root)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, RC3_TEXT, ""))

    def test_json_exact_and_byte_identical(self):
        first = run_cli(str(self.root), "--format", "json")
        second = run_cli(str(self.root), "--format", "json")
        self.assertEqual(first.stdout.encode(), second.stdout.encode())
        findings = json.loads(first.stdout)["findings"]
        self.assertEqual(
            [(f["rule_id"], f["path"], f["location"]["start_line"]) for f in findings],
            [
                ("SWIFT_FORCE_TRY", "Sources/App/Loader.swift", 3),
                ("TODO_COMMENT", "Sources/App/Loader.swift", 4),
                ("SWIFT_FORCE_CAST", "Sources/App/Loader.swift", 5),
                ("SWIFT_FORCE_TRY", "Sources/App/Loader.swift", 5),
                ("PYTHON_MUTABLE_DEFAULT", "tools/gen.py", 1),
            ],
        )
        self.assertEqual(
            findings[2],
            {
                "rule_id": "SWIFT_FORCE_CAST",
                "severity": "warning",
                "path": "Sources/App/Loader.swift",
                "location": {"start_line": 5, "end_line": None, "symbol": None},
                "message": "Forced cast operator 'as!' is used.",
                "evidence": {"operator": "as!"},
            },
        )

    def test_fail_on_thresholds(self):
        for level, code in (("info", 1), ("warning", 1), ("error", 0)):
            with self.subTest(level=level):
                self.assertEqual(run_cli(str(self.root), "--fail-on", level).returncode, code)

    def test_swift_rules_disabled(self):
        (self.root / "repo-check.toml").write_text(
            "[repo_check.rules.SWIFT_FORCE_TRY]\nenabled = false\n\n"
            "[repo_check.rules.SWIFT_FORCE_CAST]\nenabled = false\n",
            encoding="utf-8",
        )
        result = run_cli(str(self.root), "--format", "json")
        self.assertEqual(
            [f["rule_id"] for f in json.loads(result.stdout)["findings"]],
            ["TODO_COMMENT", "PYTHON_MUTABLE_DEFAULT"],
        )

    def test_invalid_swift_rule_option_exits_2(self):
        (self.root / "repo-check.toml").write_text(
            "[repo_check.rules.SWIFT_FORCE_CAST]\nseverity = \"error\"\n", encoding="utf-8"
        )
        result = run_cli(str(self.root))
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("unknown key(s) in repo_check.rules.SWIFT_FORCE_CAST: severity",
                      result.stderr)


RC4_TEXT = (
    "<repository>: warning PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE: "
    "Production source files changed without any test file changes.\n"
    "  evidence: production_files=2, test_files=0\n"
    ".github/workflows/ci.yml: warning SENSITIVE_PROJECT_FILE_CHANGED: "
    "Sensitive project file changed.\n"
    '  evidence: category="github_workflow", matched_path=".github/workflows/ci.yml", '
    'old_path=null, status="added"\n'
    "build/out.o: warning TRACKED_BUILD_ARTIFACT: Build artifact is tracked by Git.\n"
    '  evidence: classification="build_artifact", tracked=true\n'
    "lib/core.py: warning SENSITIVE_PROJECT_FILE_CHANGED: Sensitive project file changed.\n"
    '  evidence: category="python_packaging", matched_path="setup.py", old_path="setup.py", '
    'status="renamed"\n'
    "src/app.py:1: info TODO_COMMENT: Comment contains a TODO marker.\n"
    '  evidence: marker="TODO", text="TODO: validate input"\n'
    "\n"
    "5 findings (0 error, 4 warning, 1 info).\n"
)


@requires_git
class CompareCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_branch_repo(
            Path(self._tmp.name) / "repo",
            {
                "src/app.py": "x = 1\n",
                "setup.py": "".join(f"# setup line {i} for rename detection\n" for i in range(12)),
                "tests/test_app.py": "def test(): pass\n",
                "README.md": "# r\n",
            },
        )
        write_tree(self.root, {
            "src/app.py": "# TODO: validate input\nx = 2\n",
            ".github/workflows/ci.yml": "on: push\n",
            "build/out.o": b"\0",
        })
        (self.root / "lib").mkdir()
        git(self.root, "mv", "setup.py", "lib/core.py")
        commit_all(self.root, "feature")

    def tearDown(self):
        self._tmp.cleanup()

    def test_text(self):
        result = run_cli(".", "--compare", "main", cwd=self.root)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, RC4_TEXT, ""))

    def test_json_exact_and_byte_identical(self):
        first = run_cli(str(self.root), "--compare", "main", "--format", "json")
        second = run_cli(str(self.root), "--compare", "main", "--format", "json")
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout.encode(), second.stdout.encode())
        document = json.loads(first.stdout)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(
            [(f["rule_id"], f["path"]) for f in document["findings"]],
            [
                ("PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE", None),
                ("SENSITIVE_PROJECT_FILE_CHANGED", ".github/workflows/ci.yml"),
                ("TRACKED_BUILD_ARTIFACT", "build/out.o"),
                ("SENSITIVE_PROJECT_FILE_CHANGED", "lib/core.py"),
                ("TODO_COMMENT", "src/app.py"),
            ],
        )
        self.assertEqual(
            document["findings"][0],
            {
                "rule_id": "PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE",
                "severity": "warning",
                "path": None,
                "location": None,
                "message": "Production source files changed without any test file changes.",
                "evidence": {"production_files": 2, "test_files": 0},
            },
        )
        self.assertNotIn(str(self.root), first.stdout)

    def test_without_compare_there_are_no_change_findings(self):
        result = run_cli(str(self.root), "--format", "json")
        self.assertEqual(
            [f["rule_id"] for f in json.loads(result.stdout)["findings"]],
            ["TRACKED_BUILD_ARTIFACT", "TODO_COMMENT"],
        )

    def test_fail_on_thresholds(self):
        for level, code in (("info", 1), ("warning", 1), ("error", 0)):
            with self.subTest(level=level):
                result = run_cli(str(self.root), "--compare", "main", "--fail-on", level)
                self.assertEqual(result.returncode, code)

    def test_large_changeset_configuration(self):
        (self.root / "repo-check.toml").write_text(
            "[repo_check.rules.LARGE_CHANGESET]\nmax_files = 3\n", encoding="utf-8"
        )
        result = run_cli(str(self.root), "--compare", "main", "--format", "json")
        findings = json.loads(result.stdout)["findings"]
        self.assertEqual(
            findings[0],
            {
                "rule_id": "LARGE_CHANGESET",
                "severity": "warning",
                "path": None,
                "location": None,
                "message": "Changeset contains 4 files; configured maximum is 3.",
                "evidence": {"changed_files": 4, "maximum_files": 3},
            },
        )

    def test_bad_compare_requests_exit_2(self):
        cases = {
            ("--compare", "nope"): "cannot resolve comparison ref 'nope' to a local commit",
            ("--compare=--output=x",): "invalid comparison ref '--output=x'",
            ("--compare", "main..feature"): "cannot resolve comparison ref",
        }
        for args, message in cases.items():
            with self.subTest(args=args):
                result = run_cli(str(self.root), *args)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn(f"repo-check: error: {message}", result.stderr)

    def test_compare_outside_git_exits_2(self):
        plain = Path(self._tmp.name) / "plain"
        write_tree(plain, {"a.py": ""})
        result = run_cli(str(plain), "--compare", "main")
        self.assertEqual(result.returncode, 2)
        self.assertIn("is not inside a Git worktree", result.stderr)

    def test_compare_without_git_executable_exits_2(self):
        empty_bin = Path(self._tmp.name) / "empty-bin"
        empty_bin.mkdir()
        result = run_cli(
            str(self.root), "--compare", "main", env_overrides={"PATH": str(empty_bin)}
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--compare requires Git", result.stderr)


@requires_git
class HistoryCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_branch_repo(
            Path(self._tmp.name) / "repo",
            {"src/app.py": "x = 1\n", "tests/test_app.py": "def test(): pass\n"},
        )
        write_tree(self.root, {"src/app.py": "def f(x=[]):\n    return x\n"})
        commit_all(self.root, "feature")

    def tearDown(self):
        self._tmp.cleanup()

    def test_history_adds_no_output_and_keeps_json_schema(self):
        for fmt in ("text", "json"):
            with self.subTest(fmt=fmt):
                plain = run_cli(".", "--format", fmt, cwd=self.root)
                first = run_cli(".", "--history", "200", "--format", fmt, cwd=self.root)
                second = run_cli(".", "--history", "200", "--format", fmt, cwd=self.root)
                self.assertEqual((first.returncode, first.stderr), (0, ""))
                self.assertEqual(first.stdout, plain.stdout)
                self.assertEqual(first.stdout.encode(), second.stdout.encode())
        self.assertEqual(json.loads(first.stdout)["schema_version"], 1)

    def test_clean_repository_prints_no_findings(self):
        write_tree(self.root, {"src/app.py": "x = 2\n"})
        commit_all(self.root, "clean")
        result = run_cli(".", "--history", "1", cwd=self.root)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "No findings.\n", ""))

    def test_history_composes_with_compare(self):
        both = run_cli(".", "--compare", "main", "--history", "200", cwd=self.root)
        compare_only = run_cli(".", "--compare", "main", cwd=self.root)
        self.assertEqual((both.returncode, both.stderr), (compare_only.returncode, ""))
        self.assertEqual(both.stdout, compare_only.stdout)
        self.assertIn("PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE", both.stdout)

    def test_invalid_depths_exit_2(self):
        for value in ("0", "-1", "-200", "abc", "1.5", "", " 3", "+3", "0x10", "٣"):
            with self.subTest(value=value):
                result = run_cli(".", "--history", value, cwd=self.root)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("--history", result.stderr)
        missing = run_cli(".", "--history", cwd=self.root)
        self.assertEqual(missing.returncode, 2)

    def test_history_outside_a_git_worktree_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), {"a.py": "x = 1\n"})
            result = run_cli(tmp, "--history", "5")
            plain = run_cli(tmp)
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertRegex(result.stderr, r"^repo-check: error: .* is not inside a Git worktree")
        self.assertEqual(plain.returncode, 0)

    def test_repository_without_commits_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = make_git_repo(Path(tmp) / "empty", {})
            result = run_cli(str(empty), "--history", "5")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot resolve HEAD to a commit", result.stderr)

    def test_help_documents_history(self):
        self.assertIn("--history N", run_cli("--help").stdout)


@requires_git
class FrequentlyChangedFileCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = make_git_repo(Path(cls._tmp.name) / "repo",
                                 {"src/hot.py": "v = 0\n", "src/cold.py": "c = 0\n"})
        for i in range(1, 55):
            change = {"notes.txt": f"{i}\n"}
            if i < 10:
                change["src/hot.py"] = f"v = {i}\n"
            write_tree(cls.root, change)
            commit_all(cls.root, f"c{i}")
        cls.head = git(cls.root, "rev-parse", "HEAD").strip()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_text_and_exit_statuses(self):
        expected = (
            "src/hot.py: info FREQUENTLY_CHANGED_FILE: Source file was touched in 10 of 55 "
            "scanned commits.\n"
            f'  evidence: history_head="{self.head}", minimum_scanned_commits=50, '
            "minimum_touch_percent=10, minimum_touching_commits=10, requested_commits=100, "
            "scanned_commits=55, shallow=false, touching_commits=10\n"
            "\n1 finding (0 error, 0 warning, 1 info).\n"
        )
        first = run_cli(".", "--history", "100", cwd=self.root)
        second = run_cli(".", "--history", "100", cwd=self.root)
        self.assertEqual((first.returncode, first.stdout, first.stderr), (0, expected, ""))
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(run_cli(".", "--history", "100", "--fail-on", "info",
                                 cwd=self.root).returncode, 1)

    def test_no_history_and_insufficient_history(self):
        for args in ((), ("--history", "49")):
            with self.subTest(args=args):
                result = run_cli(".", *args, "--fail-on", "info", cwd=self.root)
                self.assertEqual((result.returncode, result.stdout), (0, "No findings.\n"))

    def test_json_exact_and_deterministic(self):
        first = run_cli(".", "--history", "100", "--format", "json", cwd=self.root)
        second = run_cli(".", "--history", "100", "--format", "json", cwd=self.root)
        self.assertEqual(first.stdout.encode(), second.stdout.encode())
        document = json.loads(first.stdout)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["findings"], [{
            "rule_id": "FREQUENTLY_CHANGED_FILE", "severity": "info", "path": "src/hot.py",
            "location": None,
            "message": "Source file was touched in 10 of 55 scanned commits.",
            "evidence": {"history_head": self.head, "minimum_scanned_commits": 50,
                         "minimum_touch_percent": 10, "minimum_touching_commits": 10,
                         "requested_commits": 100, "scanned_commits": 55, "shallow": False,
                         "touching_commits": 10},
        }])

    def test_config_thresholds_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "c.toml"
            config.write_text("[repo_check.rules.FREQUENTLY_CHANGED_FILE]\n"
                              "min_touching_commits = 11\n", encoding="utf-8")
            result = run_cli(".", "--history", "100", "--config", str(config), cwd=self.root)
            bad = Path(tmp) / "bad.toml"
            bad.write_text("[repo_check.rules.FREQUENTLY_CHANGED_FILE]\n"
                           "min_touch_percent = 101\n", encoding="utf-8")
            invalid = run_cli(".", "--history", "100", "--config", str(bad), cwd=self.root)
        self.assertEqual(result.stdout, "No findings.\n")
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("min_touch_percent must be an integer from 1 to 100", invalid.stderr)


class OutputEncodingCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_text_output_on_ascii_stream_escapes_instead_of_failing(self):
        write_tree(self.root, {"\u00e9.py": "# TODO: accent\n"})
        result = run_cli(str(self.root), env_overrides={"PYTHONIOENCODING": "ascii"})
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertTrue(result.stdout.startswith("\\xe9.py:1: info TODO_COMMENT: "))

    def test_undecodable_file_name_is_escaped_in_text_output(self):
        try:
            with open(os.path.join(os.fsencode(self.root), b"bad\xff.py"), "wb") as handle:
                handle.write(b"# TODO: bytes\n")
        except (OSError, ValueError):
            self.skipTest("file system does not allow non-UTF-8 file names")
        # A strict UTF-8 stream (the default in UTF-8 locales) cannot encode
        # the surrogate that stands for the undecodable byte.
        result = run_cli(str(self.root), env_overrides={"PYTHONIOENCODING": "utf-8"})
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertTrue(result.stdout.startswith("bad\\udcff.py:1: info TODO_COMMENT: "))
        document = json.loads(run_cli(str(self.root), "--format", "json").stdout)
        self.assertEqual(document["findings"][0]["path"], "bad\udcff.py")


class InProcessCliTests(unittest.TestCase):
    def test_unexpected_exception_exits_2_not_1(self):
        stderr = io.StringIO()
        with mock.patch("repo_check.cli.analyze", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(stderr):
            code = cli.main([str(FIXTURES / "clean_repository")])
        self.assertEqual(code, 2)
        self.assertIn("repo-check: internal error", stderr.getvalue())

    def test_default_fail_on_is_error(self):
        self.assertEqual(cli.build_parser().parse_args(["."]).fail_on, "error")


if __name__ == "__main__":
    unittest.main()
