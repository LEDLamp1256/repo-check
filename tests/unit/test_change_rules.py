import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_check import engine
from repo_check.config import parse_config
from repo_check.discovery import classify_path, discover
from repo_check.engine import analyze, run_rules
from repo_check.errors import ComparisonError, ConfigError
from repo_check.model import ChangedPath, ChangeSource, ChangeState, GitChange, Finding, GitComparison
from repo_check.model import ChangeStatus as S
from repo_check.model import FileClassification as K
from repo_check.model import Severity
from repo_check.rules import CHANGE_RULES
from repo_check.rules.change.large_changeset import LargeChangeset
from repo_check.rules.change.production_change import ProductionChangeWithoutTestChange
from repo_check.rules.change.sensitive_project_file import (
    SensitiveProjectFileChanged,
    sensitive_category,
)

from support import commit_all, make_branch_repo, requires_git, write_tree

A, M, D, R, CP, T = S.ADDED, S.MODIFIED, S.DELETED, S.RENAMED, S.COPIED, S.TYPE_CHANGED


def comparison(*changes):
    return GitComparison("main", "c" * 40, "b" * 40, "h" * 40, tuple(changes))


class ChangeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def snapshot(self, *changes, files=None, config=None):
        write_tree(self.root, files or {})
        return discover(self.root, parse_config(config or {}), comparison=comparison(*changes))

    def findings(self, *changes, rules=CHANGE_RULES, files=None, config=None):
        return list(run_rules(self.snapshot(*changes, files=files, config=config), (), (), rules))


class NormalizationTests(ChangeTestCase):
    def test_existing_files_use_snapshot_classification(self):
        snapshot = self.snapshot(
            GitChange(M, "src/app.py"),
            GitChange(M, "src/gen.py"),
            files={"src/app.py": "x = 1\n", "src/gen.py": "# @generated\nx = 1\n"},
        )
        self.assertEqual(
            snapshot.change,
            ChangeState(
                source=ChangeSource.COMPARISON,
                requested_ref="main",
                compare_commit="c" * 40,
                merge_base="b" * 40,
                head_commit="h" * 40,
                changes=(
                    ChangedPath(M, "src/app.py", K.SOURCE),
                    ChangedPath(M, "src/gen.py", K.GENERATED),  # content-based
                ),
            ),
        )

    def test_missing_paths_use_path_classification(self):
        snapshot = self.snapshot(
            GitChange(D, "tests/test_old.py"),
            GitChange(R, "lib/new.py", "src/old.py"),
            GitChange(D, "assets/logo.png"),
            GitChange(D, "build/out.o"),
            files={"lib/new.py": "x\n"},
        )
        self.assertEqual(
            snapshot.change.changes,
            (
                ChangedPath(D, "assets/logo.png", K.BINARY),
                ChangedPath(D, "build/out.o", K.BUILD_ARTIFACT),
                ChangedPath(R, "lib/new.py", K.SOURCE, "src/old.py", K.SOURCE),
                ChangedPath(D, "tests/test_old.py", K.TEST),
            ),
        )

    def test_classify_path_matches_classify_without_content(self):
        cases = {"src/a.py": K.SOURCE, "tests/test_a.py": K.TEST, "README.md": K.DOCUMENTATION,
                 "pyproject.toml": K.CONFIGURATION, "a.min.js": K.GENERATED,
                 "dist/x.js": K.BUILD_ARTIFACT, "logo.png": K.BINARY, "notes.txt": K.UNKNOWN,
                 "Sources/AppTests/ATests.swift": K.TEST, "src/mypkg/dist/core.py": K.SOURCE,
                 "docs/manuals/build/index.md": K.DOCUMENTATION,
                 "pkg/.build/out": K.BUILD_ARTIFACT}
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertIs(classify_path(path), expected)

    def test_exclusions_drop_entries_only_when_every_path_is_excluded(self):
        snapshot = self.snapshot(
            GitChange(A, "vendor/lib.py"),
            GitChange(R, "vendor/moved.py", "src/moved.py"),
            GitChange(M, "src/app.py"),
            config={"repo_check": {"exclude": ["vendor/"]}},
        )
        self.assertEqual(
            [(c.path, c.old_path) for c in snapshot.change.changes],
            [("src/app.py", None), ("vendor/moved.py", "src/moved.py")],
        )

    def test_no_comparison_means_no_change_state_and_no_change_rules(self):
        write_tree(self.root, {"a.py": ""})
        snapshot = discover(self.root, parse_config({}))
        self.assertIsNone(snapshot.change)
        self.assertEqual(run_rules(snapshot, (), (), CHANGE_RULES), ())


class LargeChangesetTests(ChangeTestCase):
    rules = (LargeChangeset(),)

    def added(self, count):
        return [GitChange(A, f"f{i:03}.txt") for i in range(count)]

    def expected(self, actual, maximum):
        return Finding("LARGE_CHANGESET", Severity.WARNING,
                       f"Changeset contains {actual} files; configured maximum is {maximum}.",
                       None, None, {"changed_files": actual, "maximum_files": maximum})

    def test_default_threshold(self):
        self.assertEqual(self.findings(*self.added(50), rules=self.rules), [])
        self.assertEqual(self.findings(*self.added(51), rules=self.rules), [self.expected(51, 50)])

    def test_configured_boundary(self):
        config = {"repo_check": {"rules": {"LARGE_CHANGESET": {"max_files": 2}}}}
        self.assertEqual(self.findings(*self.added(2), rules=self.rules, config=config), [])
        self.assertEqual(self.findings(*self.added(3), rules=self.rules, config=config),
                         [self.expected(3, 2)])

    def test_rename_and_copy_count_once(self):
        config = {"repo_check": {"rules": {"LARGE_CHANGESET": {"max_files": 1}}}}
        changes = [GitChange(R, "new.py", "old.py"), GitChange(CP, "b.py", "a.py")]
        self.assertEqual(self.findings(*changes, rules=self.rules, config=config),
                         [self.expected(2, 1)])

    def test_disabled(self):
        config = {"repo_check": {"rules": {"LARGE_CHANGESET": {"enabled": False}}}}
        self.assertEqual(self.findings(*self.added(60), rules=self.rules, config=config), [])

    def test_invalid_config(self):
        for value in (0, -1, "50", 1.5, True, False):
            with self.subTest(value=value), self.assertRaisesRegex(
                ConfigError, r"LARGE_CHANGESET\.max_files must be a positive integer"
            ):
                parse_config({"repo_check": {"rules": {"LARGE_CHANGESET": {"max_files": value}}}})
        with self.assertRaisesRegex(ConfigError, "max_lines"):
            parse_config({"repo_check": {"rules": {"LARGE_CHANGESET": {"max_lines": 5}}}})


class ProductionWithoutTestTests(ChangeTestCase):
    rules = (ProductionChangeWithoutTestChange(),)

    def expected(self, production):
        return Finding("PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE", Severity.WARNING,
                       "Production source files changed without any test file changes.",
                       None, None, {"production_files": production, "test_files": 0})

    def run_case(self, *changes, files=None):
        return self.findings(*changes, rules=self.rules, files=files)

    def test_production_only(self):
        self.assertEqual(
            self.run_case(
                GitChange(M, "src/app.py"),
                GitChange(A, "Sources/App/V.swift"),
                GitChange(M, "README.md"),
                files={"src/app.py": "x\n"},
            ),
            [self.expected(2)],
        )

    def test_deleted_production_source_counts(self):
        self.assertEqual(self.run_case(GitChange(D, "src/gone.py")), [self.expected(1)])

    def test_source_and_test_changes(self):
        for test_change in (GitChange(M, "tests/test_app.py"),
                            GitChange(A, "Tests/AppTests/VTests.swift"),
                            GitChange(D, "tests/test_old.py"),
                            GitChange(R, "tests/test_new.py", "tests/test_old.py"),
                            GitChange(R, "archive/old.py", "tests/test_moved.py")):
            with self.subTest(test_change=test_change):
                self.assertEqual(self.run_case(GitChange(M, "src/app.py"), test_change), [])

    def test_rename_from_source_to_test_counts_as_both(self):
        self.assertEqual(self.run_case(GitChange(R, "tests/test_a.py", "src/a.py")), [])

    def test_non_production_only_changes(self):
        for change in (GitChange(M, "tests/test_app.py"),
                       GitChange(M, "README.md"), GitChange(M, "docs/guide.md"),
                       GitChange(M, "pyproject.toml"),
                       GitChange(A, ".github/workflows/ci.yml"),
                       GitChange(M, "package-lock.json"),
                       GitChange(A, "dist/bundle.js"),
                       GitChange(A, "build/lib/app.py"), GitChange(M, "logo.png"),
                       GitChange(M, "tests/fixtures/data.json")):
            with self.subTest(change=change):
                self.assertEqual(self.run_case(change), [])

    def test_generated_by_content_is_not_production(self):
        self.assertEqual(
            self.run_case(GitChange(M, "src/api.py"), files={"src/api.py": "# @generated\n"}),
            [],
        )

    def test_no_changes(self):
        self.assertEqual(self.run_case(), [])


class SensitiveProjectFileTests(ChangeTestCase):
    rules = (SensitiveProjectFileChanged(),)

    def expected(self, path, category, status, matched=None, old_path=None):
        return Finding("SENSITIVE_PROJECT_FILE_CHANGED", Severity.WARNING,
                       "Sensitive project file changed.", path, None,
                       {"category": category, "matched_path": matched or path,
                        "old_path": old_path, "status": status})

    def test_every_allowlisted_category(self):
        cases = {
            "pyproject.toml": "python_packaging",
            "setup.py": "python_packaging",
            "setup.cfg": "python_packaging",
            "Package.swift": "swift_package",
            "Package.resolved": "swift_package",
            "Foo.xcodeproj/project.pbxproj": "xcode_project",
            "ios/App/My App.xcodeproj/project.pbxproj": "xcode_project",
            ".github/workflows/ci.yml": "github_workflow",
            ".github/workflows/release.yaml": "github_workflow",
        }
        for path, category in cases.items():
            with self.subTest(path=path):
                self.assertEqual(sensitive_category(path), category)
                self.assertEqual(self.findings(GitChange(M, path), rules=self.rules),
                                 [self.expected(path, category, "modified")])

    def test_near_misses(self):
        for path in ("docs/pyproject.toml", "sub/setup.py", "packages/a/Package.swift",
                     "Package.swift.txt", "package.swift", "Pyproject.toml", "setup.cfg.bak",
                     "project.pbxproj", "Foo/project.pbxproj", "Foo.xcodeproj/other.pbxproj",
                     "Foo.xcodeproj/project.pbxproj.orig", "Foo.xcworkspace/project.pbxproj",
                     ".github/workflow/ci.yml", ".github/workflows/readme.md",
                     ".github/workflows/sub/ci.yml", ".github/ci.yml", "github/workflows/ci.yml",
                     ".github/workflows/ci.YML", ".github/workflows/ci.yml.disabled",
                     "x/.github/workflows/ci.yml", "requirements.txt", "Podfile"):
            with self.subTest(path=path):
                self.assertIsNone(sensitive_category(path))
                self.assertEqual(self.findings(GitChange(M, path), rules=self.rules), [])

    def test_statuses(self):
        self.assertEqual(
            self.findings(GitChange(A, "setup.py"), GitChange(D, "setup.cfg"),
                          GitChange(T, "Package.resolved"), rules=self.rules),
            [
                self.expected("Package.resolved", "swift_package", "type_changed"),
                self.expected("setup.cfg", "python_packaging", "deleted"),
                self.expected("setup.py", "python_packaging", "added"),
            ],
        )

    def test_renames_into_away_and_between_sensitive_paths(self):
        self.assertEqual(
            self.findings(
                GitChange(R, "pyproject.toml", "config/pyproject.toml"),     # into
                GitChange(R, "legacy/setup.py", "setup.py"),                 # away
                GitChange(R, ".github/workflows/b.yml", ".github/workflows/a.yml"),  # both
                GitChange(CP, "Package.swift", "Templates/Package.swift"),   # copy into
                rules=self.rules,
            ),
            [
                self.expected(".github/workflows/b.yml", "github_workflow", "renamed",
                              old_path=".github/workflows/a.yml"),
                self.expected("Package.swift", "swift_package", "copied",
                              old_path="Templates/Package.swift"),
                self.expected("legacy/setup.py", "python_packaging", "renamed",
                              matched="setup.py", old_path="setup.py"),
                self.expected("pyproject.toml", "python_packaging", "renamed",
                              old_path="config/pyproject.toml"),
            ],
        )

    def test_rename_between_categories_reports_new_category_once(self):
        self.assertEqual(
            self.findings(GitChange(R, "pyproject.toml", "setup.py"), rules=self.rules),
            [self.expected("pyproject.toml", "python_packaging", "renamed", old_path="setup.py")],
        )


class CombinedAndConfigTests(ChangeTestCase):
    def test_all_rules_together_are_globally_ordered(self):
        changes = [GitChange(A, f"src/m{i}.py") for i in range(3)]
        changes.append(GitChange(M, "pyproject.toml"))
        config = {"repo_check": {"rules": {"LARGE_CHANGESET": {"max_files": 3}}}}
        self.assertEqual(
            [(f.rule_id, f.path) for f in self.findings(*changes, config=config)],
            [
                ("LARGE_CHANGESET", None),
                ("PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE", None),
                ("SENSITIVE_PROJECT_FILE_CHANGED", "pyproject.toml"),
            ],
        )

    def test_optionless_rules_reject_options_and_defaults(self):
        config = parse_config({})
        self.assertEqual(dict(config.rule("LARGE_CHANGESET").options), {"max_files": 50})
        for rule_id in ("PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE", "SENSITIVE_PROJECT_FILE_CHANGED"):
            with self.subTest(rule=rule_id):
                self.assertTrue(config.rule(rule_id).enabled)
                self.assertEqual(dict(config.rule(rule_id).options), {})
                with self.assertRaisesRegex(ConfigError, "unknown key"):
                    parse_config({"repo_check": {"rules": {rule_id: {"max_files": 1}}}})
                with self.assertRaisesRegex(ConfigError, "enabled must be a boolean"):
                    parse_config({"repo_check": {"rules": {rule_id: {"enabled": 1}}}})

    def test_disabling_each_rule(self):
        changes = [GitChange(A, f"src/m{i}.py") for i in range(3)]
        changes.append(GitChange(M, "setup.py"))
        base = {"LARGE_CHANGESET": {"max_files": 1}}
        for rule_id in ("LARGE_CHANGESET", "PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE",
                        "SENSITIVE_PROJECT_FILE_CHANGED"):
            with self.subTest(rule=rule_id):
                rules = {**base, rule_id: {**base.get(rule_id, {}), "enabled": False}}
                found = {f.rule_id for f in self.findings(
                    *changes, config={"repo_check": {"rules": rules}})}
                self.assertEqual(len(found), 2)
                self.assertNotIn(rule_id, found)


@requires_git
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_branch_repo(
            Path(self._tmp.name) / "repo",
            {"src/app.py": "x = 1\n", "tests/test_app.py": "def test(): pass\n",
             "pyproject.toml": "[project]\nname = 'x'\n", "pkg/a.py": "a\n", "pkgs/b.py": "b\n"},
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_analyze_with_compare(self):
        write_tree(
            self.repo, {"src/app.py": "x = 2\n", "pyproject.toml": "[project]\nname = 'y'\n"}
        )
        commit_all(self.repo, "change")
        result = analyze(self.repo, compare_ref="main")
        self.assertEqual(
            [(f.rule_id, f.path) for f in result.findings],
            [("PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE", None),
             ("SENSITIVE_PROJECT_FILE_CHANGED", "pyproject.toml")],
        )
        self.assertEqual(result.snapshot.change.requested_ref, "main")
        self.assertIs(result.snapshot.change.source, ChangeSource.COMPARISON)
        comparison_facts = engine.read_git_comparison(self.repo, "main")
        self.assertEqual(
            (result.snapshot.change.compare_commit, result.snapshot.change.merge_base,
             result.snapshot.change.head_commit),
            (comparison_facts.compare_commit, comparison_facts.merge_base,
             comparison_facts.head_commit),
        )

    def test_subdirectory_analysis(self):
        write_tree(self.repo, {"pkg/a.py": "a2\n", "pkgs/b.py": "b2\n", "pyproject.toml": "[x]\n"})
        commit_all(self.repo, "change")
        result = analyze(self.repo / "pkg", compare_ref="main")
        self.assertEqual([c.path for c in result.snapshot.change.changes], ["a.py"])
        self.assertEqual([f.rule_id for f in result.findings],
                         ["PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE"])

    def test_nested_dist_change_is_source(self):
        write_tree(self.repo, {"src/mypkg/dist/core.py": "x = 1\n", "dist/out.py": "y = 1\n"})
        commit_all(self.repo, "change")
        result = analyze(self.repo, compare_ref="main")
        self.assertEqual(
            [(c.path, c.classification) for c in result.snapshot.change.changes],
            [("dist/out.py", K.BUILD_ARTIFACT), ("src/mypkg/dist/core.py", K.SOURCE)],
        )
        self.assertIn("PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE",
                      [f.rule_id for f in result.findings])

    def test_one_comparison_per_run(self):
        write_tree(self.repo, {"src/app.py": "x = 2\n"})
        commit_all(self.repo, "change")
        with mock.patch.object(engine, "read_git_comparison",
                               wraps=engine.read_git_comparison) as spy:
            analyze(self.repo, compare_ref="main")
        self.assertEqual(spy.call_count, 1)

    def test_no_compare_means_no_git_comparison(self):
        with mock.patch.object(engine, "read_git_comparison") as spy:
            result = analyze(self.repo)
        spy.assert_not_called()
        self.assertIsNone(result.snapshot.change)

    def test_explicit_compare_errors_propagate(self):
        with self.assertRaises(ComparisonError):
            analyze(self.repo, compare_ref="does-not-exist")
        with tempfile.TemporaryDirectory() as plain:
            with self.assertRaisesRegex(ComparisonError, "not inside a Git worktree"):
                analyze(Path(plain), compare_ref="main")

    def test_missing_git_with_explicit_compare(self):
        with mock.patch.dict(os.environ, {"PATH": self._tmp.name}):
            with self.assertRaisesRegex(ComparisonError, "executable was not found"):
                analyze(self.repo, compare_ref="main")


if __name__ == "__main__":
    unittest.main()
