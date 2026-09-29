import math
import unittest

from repo_check.model import ChangeSource, ChangeState, Finding, GitLocalChanges, Location, Severity, canonical_evidence, sort_findings


class ChangeSourceTests(unittest.TestCase):
    def test_values_are_typed_and_explicit(self):
        self.assertEqual(
            [source.value for source in ChangeSource],
            ["comparison", "working_tree", "staged"],
        )

    def test_working_tree_needs_no_comparison_metadata(self):
        state = ChangeState(source=ChangeSource.WORKING_TREE)
        self.assertEqual(state.changes, ())
        self.assertIsNone(state.requested_ref)
        self.assertIsNone(state.compare_commit)
        self.assertIsNone(state.merge_base)
        self.assertIsNone(state.head_commit)

    def test_staged_needs_no_comparison_metadata(self):
        state = ChangeState(source=ChangeSource.STAGED)
        self.assertEqual(state.changes, ())
        self.assertIsNone(state.requested_ref)
        self.assertIsNone(state.compare_commit)
        self.assertIsNone(state.merge_base)
        self.assertIsNone(state.head_commit)


class GitLocalChangesTests(unittest.TestCase):
    def test_accepts_staged_and_working_tree_sources(self):
        for source in (ChangeSource.STAGED, ChangeSource.WORKING_TREE):
            with self.subTest(source=source):
                local = GitLocalChanges(source=source)
                self.assertIs(local.source, source)
                self.assertEqual(local.changes, ())

    def test_rejects_comparison_source(self):
        with self.assertRaisesRegex(ValueError, "staged or working-tree"):
            GitLocalChanges(source=ChangeSource.COMPARISON)


class SeverityTests(unittest.TestCase):
    def test_values_are_exactly_info_warning_error(self):
        self.assertEqual([s.value for s in Severity], ["info", "warning", "error"])

    def test_ordering(self):
        self.assertLess(Severity.INFO.rank, Severity.WARNING.rank)
        self.assertLess(Severity.WARNING.rank, Severity.ERROR.rank)

    def test_meets_threshold(self):
        self.assertTrue(Severity.WARNING.meets(Severity.INFO))
        self.assertTrue(Severity.WARNING.meets(Severity.WARNING))
        self.assertFalse(Severity.WARNING.meets(Severity.ERROR))
        self.assertTrue(Severity.ERROR.meets(Severity.ERROR))
        self.assertFalse(Severity.INFO.meets(Severity.WARNING))

    def test_parse_from_string(self):
        self.assertIs(Severity("warning"), Severity.WARNING)
        with self.assertRaises(ValueError):
            Severity("critical")


class LocationTests(unittest.TestCase):
    def test_optional_fields_default_to_none(self):
        location = Location(start_line=3)
        self.assertEqual((location.start_line, location.end_line, location.symbol), (3, None, None))

    def test_full_location(self):
        location = Location(start_line=3, end_line=9, symbol="Parser.parse")
        self.assertEqual(location.end_line, 9)
        self.assertEqual(location.symbol, "Parser.parse")

    def test_rejects_invalid_lines(self):
        with self.assertRaises(ValueError):
            Location(start_line=0)
        with self.assertRaises(ValueError):
            Location(start_line=5, end_line=4)


class FindingTests(unittest.TestCase):
    def test_equality_and_fields(self):
        a = Finding("X", Severity.INFO, "msg", "a.py", Location(1), {"n": 1})
        b = Finding("X", Severity.INFO, "msg", "a.py", Location(1), {"n": 1})
        self.assertEqual(a, b)
        self.assertIsNone(Finding("X", Severity.INFO, "msg", "a.py").location)
        self.assertEqual(Finding("X", Severity.INFO, "msg", "a.py").evidence, {})

    def test_sort_order_is_path_line_rule_message(self):
        findings = [
            Finding("B_RULE", Severity.INFO, "m", "b.py"),
            Finding("B_RULE", Severity.INFO, "m", "a.py", Location(10)),
            Finding("A_RULE", Severity.INFO, "z", "a.py", Location(10)),
            Finding("A_RULE", Severity.INFO, "a", "a.py", Location(10)),
            Finding("Z_RULE", Severity.ERROR, "m", "a.py", Location(2)),
            Finding("Z_RULE", Severity.ERROR, "m", "a.py"),
        ]
        ordered = sort_findings(findings)
        self.assertEqual(
            [(f.path, f.location.start_line if f.location else None, f.rule_id, f.message)
             for f in ordered],
            [
                ("a.py", None, "Z_RULE", "m"),
                ("a.py", 2, "Z_RULE", "m"),
                ("a.py", 10, "A_RULE", "a"),
                ("a.py", 10, "A_RULE", "z"),
                ("a.py", 10, "B_RULE", "m"),
                ("b.py", None, "B_RULE", "m"),
            ],
        )

    def test_sort_is_independent_of_input_order(self):
        findings = [
            Finding("R", Severity.INFO, "m", p) for p in ("c.py", "a.py", "b/x.py", "b.py")
        ]
        self.assertEqual(sort_findings(findings), sort_findings(list(reversed(findings))))


class RepositoryScopedFindingTests(unittest.TestCase):
    def test_repository_finding_has_no_path_or_location(self):
        finding = Finding("REPO_RULE", Severity.INFO, "Repository-level fact.", None,
                          evidence={"count": 2})
        self.assertIsNone(finding.path)
        self.assertIsNone(finding.location)
        self.assertEqual(finding.evidence, {"count": 2})

    def test_location_without_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "without a path cannot have a location"):
            Finding("REPO_RULE", Severity.INFO, "m", None, Location(1))

    def test_mixed_ordering_puts_repository_findings_first(self):
        findings = [
            Finding("A_RULE", Severity.INFO, "m", "a.py", Location(3)),
            Finding("Z_RULE", Severity.INFO, "m", None),
            Finding("A_RULE", Severity.INFO, "m", "a.py"),
            Finding("B_RULE", Severity.INFO, "b", None),
            Finding("B_RULE", Severity.INFO, "a", None),
            Finding("A_RULE", Severity.INFO, "m", " leading-space.py"),
            Finding("A_RULE", Severity.INFO, "m", ""),
        ]
        expected = [
            (None, None, "B_RULE", "a"),
            (None, None, "B_RULE", "b"),
            (None, None, "Z_RULE", "m"),
            ("", None, "A_RULE", "m"),
            (" leading-space.py", None, "A_RULE", "m"),
            ("a.py", None, "A_RULE", "m"),
            ("a.py", 3, "A_RULE", "m"),
        ]
        for ordering in (findings, list(reversed(findings))):
            self.assertEqual(
                [(f.path, f.location.start_line if f.location else None, f.rule_id, f.message)
                 for f in sort_findings(ordering)],
                expected,
            )


class EvidenceTests(unittest.TestCase):
    def test_nested_mapping_and_sequence_are_canonicalized(self):
        finding = Finding(
            "R", Severity.INFO, "m", "a.py",
            evidence={
                "zeta": [3, 1, {"b": None, "a": True}],
                "alpha": {"y": (1.5, "s"), "x": []},
            },
        )
        self.assertEqual(
            finding.evidence,
            {"alpha": {"x": [], "y": [1.5, "s"]}, "zeta": [3, 1, {"a": True, "b": None}]},
        )
        self.assertEqual(list(finding.evidence), ["alpha", "zeta"])
        self.assertEqual(list(finding.evidence["alpha"]), ["x", "y"])
        self.assertEqual(list(finding.evidence["zeta"][2]), ["a", "b"])

    def test_evidence_is_copied_from_caller(self):
        source = {"files": ["a.py"]}
        finding = Finding("R", Severity.INFO, "m", None, evidence=source)
        source["files"].append("b.py")
        self.assertEqual(finding.evidence, {"files": ["a.py"]})

    def test_scalars_are_unchanged(self):
        evidence = {"actual_lines": 1184, "maximum_lines": 1000}
        self.assertEqual(Finding("R", Severity.INFO, "m", "a.py", evidence=evidence).evidence,
                         evidence)

    def test_unsupported_values_are_rejected(self):
        cases = [
            ({"s": {1, 2}}, TypeError),
            ({"b": b"bytes"}, TypeError),
            ({"o": object()}, TypeError),
            ({"e": Severity.INFO}, TypeError),
            ({"nested": {1: "non-str key"}}, TypeError),
            ({"list": [frozenset()]}, TypeError),
            ({"nan": math.nan}, ValueError),
            ({"inf": [math.inf]}, ValueError),
        ]
        for evidence, error in cases:
            with self.subTest(evidence=evidence), self.assertRaises(error):
                Finding("R", Severity.INFO, "m", "a.py", evidence=evidence)

    def test_evidence_must_be_mapping(self):
        with self.assertRaises(TypeError):
            Finding("R", Severity.INFO, "m", "a.py", evidence=[1, 2])
        with self.assertRaises(TypeError):
            Finding("R", Severity.INFO, "m", "a.py", evidence={1: "x"})

    def test_error_names_offending_location(self):
        with self.assertRaisesRegex(TypeError, r"evidence\.outer\[1\]"):
            canonical_evidence({"outer": [1, {2}]})


if __name__ == "__main__":
    unittest.main()
