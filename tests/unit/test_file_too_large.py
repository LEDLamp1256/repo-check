import tempfile
import unittest
from pathlib import Path

from repo_check.config import default_config, parse_config
from repo_check.discovery import discover
from repo_check.engine import analyze, run_rules
from repo_check.model import Finding, Severity
from repo_check.rules import ALL_RULES, FILE_RULES
from repo_check.rules.common.file_too_large import FileTooLarge

from support import FIXTURES, numbered_lines, write_tree


def expected(path: str, actual: int, maximum: int) -> Finding:
    return Finding(
        rule_id="FILE_TOO_LARGE",
        severity=Severity.WARNING,
        message=f"File contains {actual} lines; configured maximum is {maximum}.",
        path=path,
        location=None,
        evidence={"actual_lines": actual, "maximum_lines": maximum},
    )


class MetadataTests(unittest.TestCase):
    def test_metadata(self):
        metadata = FileTooLarge.metadata
        self.assertEqual(metadata.id, "FILE_TOO_LARGE")
        self.assertEqual(metadata.default_severity, Severity.WARNING)
        self.assertTrue(metadata.title and metadata.category)
        self.assertTrue(metadata.description and metadata.remediation)

    def test_registered_rules(self):
        self.assertEqual(
            [rule.metadata.id for rule in ALL_RULES],
            [
                "BROKEN_LOCAL_DOC_LINK",
                "FILE_TOO_LARGE",
                "FREQUENTLY_CHANGED_FILE",
                "LARGE_CHANGESET",
                "PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE",
                "PYTHON_BARE_EXCEPT",
                "PYTHON_CLASS_TOO_LARGE",
                "PYTHON_FUNCTION_TOO_LARGE",
                "PYTHON_MUTABLE_DEFAULT",
                "SENSITIVE_PROJECT_FILE_CHANGED",
                "SWIFT_FORCE_CAST",
                "SWIFT_FORCE_TRY",
                "TODO_COMMENT",
                "TRACKED_BUILD_ARTIFACT",
            ],
        )
        self.assertEqual(
            [rule.metadata.id for rule in FILE_RULES],
            [
                "FILE_TOO_LARGE",
                "TODO_COMMENT",
                "PYTHON_BARE_EXCEPT",
                "PYTHON_CLASS_TOO_LARGE",
                "PYTHON_FUNCTION_TOO_LARGE",
                "PYTHON_MUTABLE_DEFAULT",
                "SWIFT_FORCE_CAST",
                "SWIFT_FORCE_TRY",
            ],
        )


class FileTooLargeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def findings(self, config=None):
        snapshot = discover(self.root, config or default_config())
        return list(run_rules(snapshot, FILE_RULES))

    def test_default_threshold_positive(self):
        write_tree(self.root, {"src/big.py": numbered_lines(1001)})
        self.assertEqual(self.findings(), [expected("src/big.py", 1001, 1000)])

    def test_default_threshold_exactly_at_limit(self):
        write_tree(self.root, {"src/exact.py": numbered_lines(1000)})
        self.assertEqual(self.findings(), [])

    def test_default_threshold_below_limit(self):
        write_tree(self.root, {"src/small.py": numbered_lines(999)})
        self.assertEqual(self.findings(), [])

    def test_trailing_line_without_newline_counts(self):
        write_tree(self.root, {"src/big.py": numbered_lines(1000) + "tail = 1"})
        self.assertEqual(self.findings(), [expected("src/big.py", 1001, 1000)])

    def test_test_files_are_checked(self):
        write_tree(self.root, {"tests/test_big.py": numbered_lines(1001)})
        self.assertEqual(self.findings(), [expected("tests/test_big.py", 1001, 1000)])

    def test_threshold_override(self):
        write_tree(self.root, {"a.py": numbered_lines(11), "b.py": numbered_lines(10)})
        config = parse_config({"repo_check": {"rules": {"FILE_TOO_LARGE": {"max_lines": 10}}}})
        self.assertEqual(self.findings(config), [expected("a.py", 11, 10)])

    def test_disabled_rule(self):
        write_tree(self.root, {"src/big.py": numbered_lines(5000)})
        config = parse_config({"repo_check": {"rules": {"FILE_TOO_LARGE": {"enabled": False}}}})
        self.assertEqual(self.findings(config), [])

    def test_excluded_file(self):
        write_tree(self.root, {"vendor/big.py": numbered_lines(1001)})
        config = parse_config({"repo_check": {"exclude": ["vendor/"]}})
        self.assertEqual(self.findings(config), [])

    def test_binary_file(self):
        write_tree(self.root, {"src/blob.py": b"\0" + b"x\n" * 2000})
        self.assertEqual(self.findings(), [])

    def test_generated_and_build_artifacts(self):
        big = numbered_lines(1001)
        write_tree(
            self.root,
            {
                "build/lib/pkg/mod.py": big,
                "dist/bundle.js": big,
                "static/app.min.js": big,
                "package-lock.json": big,
                "proto/api_pb2.py": big,
                "gen/client.py": "# @generated\n" + big,
            },
        )
        self.assertEqual(self.findings(), [])

    def test_non_code_files_are_not_checked(self):
        big = numbered_lines(1001)
        write_tree(
            self.root,
            {"CHANGELOG.md": big, "data/values.json": big, "data/rows.csv": big, "ci.yml": big},
        )
        self.assertEqual(self.findings(), [])

    def test_multiple_findings_are_sorted(self):
        big = numbered_lines(1001)
        write_tree(self.root, {"z.py": big, "a/b.py": big, "a.py": numbered_lines(1500)})
        self.assertEqual(
            self.findings(),
            [
                expected("a.py", 1500, 1000),
                expected("a/b.py", 1001, 1000),
                expected("z.py", 1001, 1000),
            ],
        )


class FixtureTests(unittest.TestCase):
    def test_oversized_file_fixture(self):
        result = analyze(FIXTURES / "oversized_file")
        self.assertEqual(
            list(result.findings),
            [expected("src/over.py", 6, 5), expected("tests/test_over.py", 7, 5)],
        )

    def test_exclusions_fixture(self):
        result = analyze(FIXTURES / "exclusions")
        self.assertEqual(
            list(result.findings),
            [expected("scripts/tool.py", 4, 3), expected("src/main.py", 4, 3)],
        )

    def test_clean_fixture(self):
        self.assertEqual(analyze(FIXTURES / "clean_repository").findings, ())


if __name__ == "__main__":
    unittest.main()
