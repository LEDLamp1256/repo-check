import tempfile
import unittest
from pathlib import Path

from repo_check.config import parse_config
from repo_check.discovery import discover
from repo_check.engine import analyze, run_rules
from repo_check.errors import ConfigError
from repo_check.model import FileClassification, Finding, HistoryCommit, HistoryState, Severity
from repo_check.rules import ALL_RULES, HISTORY_RULES
from repo_check.rules.history.frequently_changed_file import FrequentlyChangedFile

from support import commit_all, git, make_git_repo, requires_git, write_tree

RULE = "FREQUENTLY_CHANGED_FILE"
HEAD = "f" * 40


def history(scanned, touches, requested=None, head=HEAD, shallow=False, extra=()):
    """``scanned`` commits; commit i touches every path whose count is > i.
    ``extra`` paths are added to every commit (e.g. historical-only paths)."""
    commits = tuple(
        HistoryCommit(
            f"{i + 1:040x}",
            tuple(sorted({p for p, n in touches.items() if n > i} | set(extra))),
        )
        for i in range(scanned)
    )
    return HistoryState(requested if requested is not None else scanned, head, commits, shallow)


def expected(path, touching, scanned, requested=None, head=HEAD, shallow=False,
             minimums=(10, 10, 50)):
    return Finding(
        rule_id=RULE,
        severity=Severity.INFO,
        message=f"Source file was touched in {touching} of {scanned} scanned commits.",
        path=path,
        evidence={
            "touching_commits": touching,
            "scanned_commits": scanned,
            "requested_commits": requested if requested is not None else scanned,
            "minimum_touching_commits": minimums[0],
            "minimum_touch_percent": minimums[1],
            "minimum_scanned_commits": minimums[2],
            "history_head": head,
            "shallow": shallow,
        },
    )


class RuleTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_tree(self.root, {"src/app.py": "x = 1\n", "src/other.py": "y = 1\n"})

    def tearDown(self):
        self._tmp.cleanup()

    def findings(self, hist, options=None, exclude=None):
        section = {}
        if options is not None:
            section["rules"] = {RULE: options}
        if exclude is not None:
            section["exclude"] = exclude
        snapshot = discover(self.root, parse_config({"repo_check": section}), history=hist)
        return list(run_rules(snapshot, (), (), (), [FrequentlyChangedFile()]))


class MetadataTests(unittest.TestCase):
    def test_metadata(self):
        metadata = FrequentlyChangedFile.metadata
        self.assertEqual(
            (metadata.id, metadata.title, metadata.category, metadata.default_severity),
            (RULE, "Frequently changed source file", "maintainability", Severity.INFO),
        )
        self.assertTrue(metadata.description and metadata.remediation)

    def test_registered_as_history_rule_only(self):
        self.assertEqual([r.metadata.id for r in HISTORY_RULES], [RULE])
        self.assertIn(RULE, [r.metadata.id for r in ALL_RULES])
        self.assertTrue(hasattr(HISTORY_RULES[0], "check_history"))
        for method in ("check_file", "check_repository", "check_change"):
            self.assertFalse(hasattr(HISTORY_RULES[0], method))


class PopulationTests(RuleTestCase):
    def test_only_current_source_files_trigger(self):
        files = {
            "tests/test_app.py": "def test(): pass\n",          # TEST
            "README.md": "# r\n",                               # DOCUMENTATION
            "pyproject.toml": "[project]\n",                    # CONFIGURATION
            "package-lock.json": "{}\n",                        # GENERATED
            "build/out.py": "x = 1\n",                          # BUILD_ARTIFACT
            "logo.png": b"\x89PNG\0",                           # BINARY
            "notes.txt": "n\n",                                 # UNKNOWN
        }
        write_tree(self.root, files)
        snapshot = discover(self.root, parse_config({}))
        classes = {f.relative_path: f.classification for f in snapshot.files}
        self.assertEqual(
            {classes[p] for p in files},
            set(FileClassification) - {FileClassification.SOURCE},
        )
        self.assertIs(classes["src/app.py"], FileClassification.SOURCE)
        touches = {path: 50 for path in (*files, "src/app.py")}
        self.assertEqual(self.findings(history(50, touches)), [expected("src/app.py", 50, 50)])

    def test_historical_only_paths_do_not_trigger(self):
        # Deleted or renamed-away paths, and paths never discovered.
        hist = history(60, {"src/app.py": 5}, extra=("src/deleted.py", "old/name.py"))
        self.assertEqual(self.findings(hist), [])

    def test_excluded_current_path_does_not_trigger(self):
        hist = history(50, {"src/app.py": 50, "src/other.py": 50})
        self.assertEqual(
            self.findings(hist, exclude=["src/app.py"]), [expected("src/other.py", 50, 50)]
        )


class ThresholdTests(RuleTestCase):
    def assert_flags(self, scanned, touching, flagged):
        result = self.findings(history(scanned, {"src/app.py": touching}))
        self.assertEqual(result, [expected("src/app.py", touching, scanned)] if flagged else [])

    def test_minimum_scanned_history(self):
        self.assert_flags(49, 49, False)  # every commit touches it
        self.assert_flags(50, 10, True)

    def test_absolute_threshold(self):
        self.assert_flags(50, 9, False)
        self.assert_flags(50, 10, True)
        self.assert_flags(60, 9, False)

    def test_integer_ratio_boundaries(self):
        cases = [(100, 9, False), (100, 10, True), (101, 10, False), (101, 11, True),
                 (50, 10, True), (500, 49, False), (500, 50, True)]
        for scanned, touching, flagged in cases:
            with self.subTest(scanned=scanned, touching=touching):
                self.assert_flags(scanned, touching, flagged)

    def test_custom_options(self):
        hist = history(20, {"src/app.py": 3})
        self.assertEqual(self.findings(hist), [])  # below every default
        options = {"min_touching_commits": 3, "min_touch_percent": 15,
                   "min_scanned_commits": 20}
        self.assertEqual(self.findings(hist, options),
                         [expected("src/app.py", 3, 20, minimums=(3, 15, 20))])
        for key, value in (("min_touching_commits", 4), ("min_touch_percent", 16),
                           ("min_scanned_commits", 21)):
            with self.subTest(key=key):
                self.assertEqual(self.findings(hist, {**options, key: value}), [])

    def test_percent_extremes(self):
        hist = history(50, {"src/app.py": 10, "src/other.py": 50})
        self.assertEqual(
            [f.path for f in self.findings(hist, {"min_touch_percent": 1})],
            ["src/app.py", "src/other.py"],
        )
        self.assertEqual(
            [f.path for f in self.findings(hist, {"min_touch_percent": 100})],
            ["src/other.py"],
        )

    def test_disabled(self):
        self.assertEqual(self.findings(history(50, {"src/app.py": 50}), {"enabled": False}), [])


class EvidenceAndHistorySemanticsTests(RuleTestCase):
    def test_exact_evidence_without_percent(self):
        finding = self.findings(history(80, {"src/app.py": 12}, requested=200))[0]
        self.assertEqual(
            dict(finding.evidence),
            {"touching_commits": 12, "scanned_commits": 80, "requested_commits": 200,
             "minimum_touching_commits": 10, "minimum_touch_percent": 10,
             "minimum_scanned_commits": 50, "history_head": HEAD, "shallow": False},
        )
        self.assertNotIn("touch_percent", finding.evidence)
        self.assertIsNone(finding.location)

    def test_shallow_history_can_trigger(self):
        hist = history(50, {"src/app.py": 20}, requested=500, shallow=True)
        self.assertEqual(self.findings(hist),
                         [expected("src/app.py", 20, 50, requested=500, shallow=True)])

    def test_merge_head_is_reported_but_not_counted(self):
        merge = "e" * 40  # a merge HEAD is the anchor but not in commits
        hist = history(50, {"src/app.py": 10}, head=merge)
        self.assertNotIn(merge, [c.commit for c in hist.commits])
        self.assertEqual(self.findings(hist), [expected("src/app.py", 10, 50, head=merge)])

    def test_empty_commits_count_in_the_denominator(self):
        # 10 touching commits plus 91 commits that touched nothing here.
        hist = history(101, {"src/app.py": 10})
        self.assertEqual(sum(1 for c in hist.commits if not c.touched_paths), 91)
        self.assertEqual(self.findings(hist), [])  # 10 of 101 is below 10%
        self.assertEqual(self.findings(history(100, {"src/app.py": 10})),
                         [expected("src/app.py", 10, 100)])

    def test_each_commit_counts_once(self):
        commits = tuple(HistoryCommit(f"{i + 1:040x}", ("src/app.py", "src/other.py"))
                        for i in range(10))
        commits += tuple(HistoryCommit(f"{i + 11:040x}", ()) for i in range(40))
        self.assertEqual(self.findings(HistoryState(50, HEAD, commits)),
                         [expected("src/app.py", 10, 50), expected("src/other.py", 10, 50)])

    def test_findings_are_sorted_and_deterministic(self):
        hist = history(50, {"src/other.py": 30, "src/app.py": 20})
        first = self.findings(hist)
        self.assertEqual([f.path for f in first], ["src/app.py", "src/other.py"])
        self.assertEqual(self.findings(hist), first)


class ConfigTests(unittest.TestCase):
    def rule_config(self, options):
        return parse_config({"repo_check": {"rules": {RULE: options}}}).rule(RULE)

    def test_defaults(self):
        rule = parse_config({}).rule(RULE)
        self.assertTrue(rule.enabled)
        self.assertEqual(dict(rule.options), {"min_touching_commits": 10,
                                              "min_touch_percent": 10,
                                              "min_scanned_commits": 50})

    def test_valid_values(self):
        for options in ({"min_touch_percent": 1}, {"min_touch_percent": 100},
                        {"min_touching_commits": 1}, {"min_scanned_commits": 1},
                        {"min_touching_commits": 25, "min_scanned_commits": 200}):
            with self.subTest(options=options):
                rule = self.rule_config(options)
                for key, value in options.items():
                    self.assertEqual(rule.options[key], value)

    def test_invalid_values(self):
        invalid = {
            "min_touching_commits": (0, -1, True, False, "10", 1.5, None),
            "min_scanned_commits": (0, -5, True, "50", 50.0),
            "min_touch_percent": (0, 101, -1, True, "10", 10.0),
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                    self.rule_config({key: value})

    def test_unknown_option(self):
        with self.assertRaisesRegex(ConfigError, "unknown key"):
            self.rule_config({"min_touches": 10})


def build_history_repo(root, touching=12, total=60, subdir_other=False):
    """A repository with ``total`` commits; src/hot.py is touched by the
    first ``touching`` of them, and every commit touches a filler file."""
    repo = make_git_repo(root, {"src/hot.py": "v = 0\n", "src/cold.py": "c = 0\n",
                                "tests/test_hot.py": "def test(): pass\n"})
    for i in range(1, total):
        change = {"notes/log.txt" if not subdir_other else "other/log.txt": f"{i}\n"}
        if i < touching:
            change["src/hot.py"] = f"v = {i}\n"
        write_tree(repo, change)
        commit_all(repo, f"c{i}")
    return repo


@requires_git
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = build_history_repo(Path(cls._tmp.name) / "repo")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def rule_findings(self, result):
        return [f for f in result.findings if f.rule_id == RULE]

    def test_no_history_means_no_history_rule(self):
        self.assertEqual(self.rule_findings(analyze(self.repo)), [])

    def test_insufficient_scanned_history(self):
        result = analyze(self.repo, history_commits=49)
        self.assertEqual(len(result.snapshot.history.commits), 49)
        self.assertEqual(self.rule_findings(result), [])

    def test_sufficient_history_reports_current_source_file(self):
        result = analyze(self.repo, history_commits=200)
        head = git(self.repo, "rev-parse", "HEAD").strip()
        # 60 commits in total; the root commit and 11 more touched src/hot.py.
        self.assertEqual(self.rule_findings(result),
                         [expected("src/hot.py", 12, 60, requested=200, head=head)])

    def test_subdirectory_uses_repository_wide_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_history_repo(Path(tmp) / "repo", subdir_other=True)
            result = analyze(repo / "src", history_commits=100)
        findings = self.rule_findings(result)
        self.assertEqual([f.path for f in findings], ["hot.py"])
        # All 60 repository commits count, not only the 12 touching src/.
        self.assertEqual(findings[0].evidence["scanned_commits"], 60)

    def test_composes_with_compare(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = build_history_repo(Path(tmp) / "repo")
            git(repo, "checkout", "-q", "-b", "feature")
            write_tree(repo, {"src/hot.py": "v = 'feature'\n"})
            commit_all(repo, "feature")
            both = analyze(repo, compare_ref="main", history_commits=100)
        rule_ids = [f.rule_id for f in both.findings]
        self.assertIn("PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE", rule_ids)
        self.assertIn(RULE, rule_ids)
        self.assertEqual([c.path for c in both.snapshot.change.changes], ["src/hot.py"])


if __name__ == "__main__":
    unittest.main()
