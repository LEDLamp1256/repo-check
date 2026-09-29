import tempfile
import unittest
from pathlib import Path

from repo_check.config import default_config, parse_config
from repo_check.discovery import discover
from repo_check.engine import run_rules
from repo_check.errors import RepoCheckError
from repo_check.languages import ADAPTERS, adapter_for
from repo_check.languages.python import PYTHON
from repo_check.languages.swift import SWIFT
from repo_check.model import Finding, HistoryCommit, HistoryState, RuleMetadata, Severity
from repo_check.rules import ALL_RULES, HISTORY_RULES

from support import write_tree


class RecordingRule:
    """Test double that records which files the engine hands it."""

    metadata = RuleMetadata("FILE_TOO_LARGE", "t", "c", Severity.INFO, "d", "r")

    def __init__(self, emit_rule_id="FILE_TOO_LARGE"):
        self.seen = []
        self.emit_rule_id = emit_rule_id

    def check_file(self, file, context):
        self.seen.append(file.relative_path)
        yield Finding(self.emit_rule_id, Severity.INFO, "seen", file.relative_path)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_tree(self.root, {"b.py": "", "a.py": "", "vendor/x.py": "", ".git/HEAD": ""})

    def tearDown(self):
        self._tmp.cleanup()

    def test_rules_receive_only_snapshot_files(self):
        config = parse_config({"repo_check": {"exclude": ["vendor/"]}})
        rule = RecordingRule()
        findings = run_rules(discover(self.root, config), [rule])
        self.assertEqual(rule.seen, ["a.py", "b.py"])
        self.assertEqual([f.path for f in findings], ["a.py", "b.py"])

    def test_disabled_rule_is_not_invoked(self):
        config = parse_config({"repo_check": {"rules": {"FILE_TOO_LARGE": {"enabled": False}}}})
        rule = RecordingRule()
        self.assertEqual(run_rules(discover(self.root, config), [rule]), ())
        self.assertEqual(rule.seen, [])

    def test_mismatched_rule_id_is_rejected(self):
        config = parse_config({})
        with self.assertRaises(RepoCheckError):
            run_rules(discover(self.root, config), [RecordingRule(emit_rule_id="OTHER")])


class RecordingRepositoryRule:
    """Repository-rule test double; records the snapshots it receives."""

    metadata = RuleMetadata("TRACKED_BUILD_ARTIFACT", "t", "c", Severity.INFO, "d", "r")

    def __init__(self, emit_rule_id="TRACKED_BUILD_ARTIFACT", paths=(None, "b.py")):
        self.snapshots = []
        self.emit_rule_id = emit_rule_id
        self.paths = paths

    def check_repository(self, snapshot, context):
        self.snapshots.append(snapshot)
        for path in self.paths:
            yield Finding(self.emit_rule_id, Severity.WARNING, "repo", path)


class RepositoryRuleEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_tree(self.root, {"b.py": "", "a.py": ""})

    def tearDown(self):
        self._tmp.cleanup()

    def test_repository_rule_runs_once_with_snapshot(self):
        snapshot = discover(self.root, parse_config({}))
        rule = RecordingRepositoryRule()
        findings = run_rules(snapshot, (), [rule])
        self.assertEqual(rule.snapshots, [snapshot])
        self.assertEqual(
            [(f.path, f.rule_id) for f in findings],
            [(None, "TRACKED_BUILD_ARTIFACT"), ("b.py", "TRACKED_BUILD_ARTIFACT")],
        )

    def test_file_and_repository_rules_combine_with_global_sorting(self):
        snapshot = discover(self.root, parse_config({}))
        findings = run_rules(snapshot, [RecordingRule()], [RecordingRepositoryRule()])
        self.assertEqual(
            [(f.path, f.rule_id, f.message) for f in findings],
            [
                (None, "TRACKED_BUILD_ARTIFACT", "repo"),
                ("a.py", "FILE_TOO_LARGE", "seen"),
                ("b.py", "FILE_TOO_LARGE", "seen"),
                ("b.py", "TRACKED_BUILD_ARTIFACT", "repo"),
            ],
        )
        rerun = run_rules(snapshot, [RecordingRule()], [RecordingRepositoryRule()])
        self.assertEqual(findings, rerun)

    def test_disabled_repository_rule_is_not_invoked(self):
        config = parse_config(
            {"repo_check": {"rules": {"TRACKED_BUILD_ARTIFACT": {"enabled": False}}}}
        )
        rule = RecordingRepositoryRule()
        self.assertEqual(run_rules(discover(self.root, config), (), [rule]), ())
        self.assertEqual(rule.snapshots, [])

    def test_disabling_one_scope_leaves_the_other_running(self):
        config = parse_config(
            {"repo_check": {"rules": {"TRACKED_BUILD_ARTIFACT": {"enabled": False}}}}
        )
        findings = run_rules(discover(self.root, config), [RecordingRule()],
                             [RecordingRepositoryRule()])
        self.assertEqual([f.rule_id for f in findings], ["FILE_TOO_LARGE", "FILE_TOO_LARGE"])

    def test_mismatched_repository_rule_id_is_rejected(self):
        snapshot = discover(self.root, parse_config({}))
        with self.assertRaisesRegex(RepoCheckError, "produced a finding for OTHER"):
            run_rules(snapshot, (), [RecordingRepositoryRule(emit_rule_id="OTHER")])


class RecordingHistoryRule:
    """History-rule test double; records the snapshots it receives."""

    metadata = RuleMetadata("TODO_COMMENT", "t", "c", Severity.INFO, "d", "r")

    def __init__(self, emit_rule_id="TODO_COMMENT"):
        self.snapshots = []
        self.emit_rule_id = emit_rule_id

    def check_history(self, snapshot, context):
        self.snapshots.append(snapshot)
        yield Finding(self.emit_rule_id, Severity.INFO, "history", None)


class HistoryRuleEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_tree(self.root, {"a.py": ""})
        self.history = HistoryState(5, "a" * 40, (HistoryCommit("a" * 40, ("a.py",)),))

    def tearDown(self):
        self._tmp.cleanup()

    def test_history_rule_does_not_run_without_history(self):
        rule = RecordingHistoryRule()
        snapshot = discover(self.root, parse_config({}))
        self.assertEqual(run_rules(snapshot, (), (), (), [rule]), ())
        self.assertEqual(rule.snapshots, [])

    def test_history_rule_runs_once_when_history_exists(self):
        rule = RecordingHistoryRule()
        snapshot = discover(self.root, parse_config({}), history=self.history)
        findings = run_rules(snapshot, (), (), (), [rule])
        self.assertEqual(rule.snapshots, [snapshot])
        self.assertEqual([(f.rule_id, f.path) for f in findings], [("TODO_COMMENT", None)])

    def test_disabled_history_rule_is_not_invoked(self):
        rule = RecordingHistoryRule()
        config = parse_config({"repo_check": {"rules": {"TODO_COMMENT": {"enabled": False}}}})
        snapshot = discover(self.root, config, history=self.history)
        self.assertEqual(run_rules(snapshot, (), (), (), [rule]), ())
        self.assertEqual(rule.snapshots, [])

    def test_mismatched_history_rule_id_is_rejected(self):
        snapshot = discover(self.root, parse_config({}), history=self.history)
        with self.assertRaisesRegex(RepoCheckError, "produced a finding for OTHER"):
            run_rules(snapshot, (), (), (), [RecordingHistoryRule(emit_rule_id="OTHER")])

    def test_history_registry(self):
        self.assertEqual([r.metadata.id for r in HISTORY_RULES], ["FREQUENTLY_CHANGED_FILE"])
        # An empty history-rule sequence remains valid.
        snapshot = discover(self.root, parse_config({}), history=self.history)
        self.assertEqual(run_rules(snapshot, (), (), (), ()), ())


class RegistryTests(unittest.TestCase):
    def test_every_registered_rule_has_default_config(self):
        rule_ids = [rule.metadata.id for rule in ALL_RULES]
        self.assertEqual(sorted(rule_ids), sorted(default_config().rules))
        self.assertEqual(len(rule_ids), len(set(rule_ids)))

    def test_rule_ids_are_stable_uppercase_identifiers(self):
        for rule in ALL_RULES:
            self.assertRegex(rule.metadata.id, r"^[A-Z][A-Z0-9_]*$")


class LanguageSeamTests(unittest.TestCase):
    def test_python_and_swift_adapters_registered(self):
        self.assertEqual(ADAPTERS, (PYTHON, SWIFT))
        self.assertEqual(PYTHON.name, "python")
        self.assertEqual(PYTHON.extensions, frozenset({".py", ".pyi"}))
        self.assertEqual(SWIFT.name, "swift")
        self.assertEqual(SWIFT.extensions, frozenset({".swift"}))
        self.assertIs(adapter_for("src/app.py"), PYTHON)
        self.assertIs(adapter_for("stubs/app.PYI"), PYTHON)
        self.assertIs(adapter_for("Sources/App.swift"), SWIFT)
        self.assertIs(adapter_for("Sources/App.SWIFT"), SWIFT)
        self.assertIsNone(adapter_for("Package.resolved"))
        self.assertIsNone(adapter_for("App.swiftinterface"))
        self.assertIsNone(adapter_for("README.md"))


if __name__ == "__main__":
    unittest.main()
