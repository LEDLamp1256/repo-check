import json
import unittest

from repo_check.model import Finding, Location, Severity, sort_findings
from repo_check.output import render_json, render_text

FINDINGS = (
    Finding(
        rule_id="FILE_TOO_LARGE",
        severity=Severity.WARNING,
        message="File contains 1184 lines; configured maximum is 1000.",
        path="src/big.py",
        evidence={"maximum_lines": 1000, "actual_lines": 1184},
    ),
    Finding(
        rule_id="EXAMPLE_RULE",
        severity=Severity.ERROR,
        message="Example.",
        path="src/other.py",
        location=Location(start_line=3, end_line=7, symbol="Widget.run"),
        evidence={"name": "a, b", "flag": True, "missing": None},
    ),
    Finding(
        rule_id="EXAMPLE_RULE",
        severity=Severity.INFO,
        message="Single line.",
        path="src/other.py",
        location=Location(start_line=9),
    ),
)


REPOSITORY_FINDING = Finding(
    rule_id="REPO_RULE",
    severity=Severity.INFO,
    message="Repository-level fact.",
    path=None,
    evidence={"count": 2},
)

NESTED_FINDING = Finding(
    rule_id="NESTED_RULE",
    severity=Severity.WARNING,
    message="Nested evidence.",
    path="src/app.py",
    evidence={
        "zeta": ["b.py", "a.py"],
        "alpha": {"y": [2, {"k2": None, "k1": 1.5}], "x": "text"},
    },
)


class TextOutputTests(unittest.TestCase):
    def test_no_findings(self):
        self.assertEqual(render_text(()), "No findings.\n")

    def test_exact_text(self):
        self.assertEqual(
            render_text(FINDINGS),
            "src/big.py: warning FILE_TOO_LARGE: "
            "File contains 1184 lines; configured maximum is 1000.\n"
            "  evidence: actual_lines=1184, maximum_lines=1000\n"
            "src/other.py:3-7 (Widget.run): error EXAMPLE_RULE: Example.\n"
            '  evidence: flag=true, missing=null, name="a, b"\n'
            "src/other.py:9: info EXAMPLE_RULE: Single line.\n"
            "\n"
            "3 findings (1 error, 1 warning, 1 info).\n",
        )

    def test_single_finding_summary(self):
        self.assertTrue(
            render_text(FINDINGS[:1]).endswith("\n1 finding (0 error, 1 warning, 0 info).\n")
        )

    def test_deterministic(self):
        self.assertEqual(render_text(FINDINGS), render_text(FINDINGS))

    def test_repository_finding(self):
        self.assertEqual(
            render_text((REPOSITORY_FINDING,)),
            "<repository>: info REPO_RULE: Repository-level fact.\n"
            "  evidence: count=2\n"
            "\n"
            "1 finding (0 error, 0 warning, 1 info).\n",
        )

    def test_nested_evidence(self):
        text = render_text((NESTED_FINDING,))
        self.assertEqual(
            text.splitlines()[1],
            '  evidence: alpha={"x": "text", "y": [2, {"k1": 1.5, "k2": null}]}, '
            'zeta=["b.py", "a.py"]',
        )
        self.assertEqual(text, render_text((NESTED_FINDING,)))

    def test_control_characters_and_surrogates_are_escaped(self):
        # Paths come from the analyzed tree: a newline in a file name must not
        # start a new output line, and undecodable bytes (surrogateescape)
        # must not make the output unencodable. Ordinary Unicode is unchanged.
        finding = Finding(
            rule_id="EXAMPLE_RULE",
            severity=Severity.INFO,
            message="Link target does not exist: a\x1b[31mb\tc",
            path="new\nline/bad\udcff/\u00e9.py",
            location=Location(start_line=2, symbol="f\x7f"),
            evidence={"text": "x\ny"},
        )
        self.assertEqual(
            render_text((finding,)),
            "new\\nline/bad\\udcff/\u00e9.py:2 (f\\x7f): info EXAMPLE_RULE: "
            "Link target does not exist: a\\x1b[31mb\\tc\n"
            '  evidence: text="x\\ny"\n'
            "\n"
            "1 finding (0 error, 0 warning, 1 info).\n",
        )
        # JSON keeps exact values (escaped by JSON itself).
        document = json.loads(render_json((finding,)))
        self.assertEqual(document["findings"][0]["path"], "new\nline/bad\udcff/\u00e9.py")


class JsonOutputTests(unittest.TestCase):
    def test_no_findings(self):
        self.assertEqual(render_json(()), '{\n  "schema_version": 1,\n  "findings": []\n}\n')

    def test_structure_and_field_order(self):
        document = json.loads(render_json(FINDINGS))
        self.assertEqual(list(document), ["schema_version", "findings"])
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(
            document["findings"],
            [
                {
                    "rule_id": "FILE_TOO_LARGE",
                    "severity": "warning",
                    "path": "src/big.py",
                    "location": None,
                    "message": "File contains 1184 lines; configured maximum is 1000.",
                    "evidence": {"actual_lines": 1184, "maximum_lines": 1000},
                },
                {
                    "rule_id": "EXAMPLE_RULE",
                    "severity": "error",
                    "path": "src/other.py",
                    "location": {"start_line": 3, "end_line": 7, "symbol": "Widget.run"},
                    "message": "Example.",
                    "evidence": {"flag": True, "missing": None, "name": "a, b"},
                },
                {
                    "rule_id": "EXAMPLE_RULE",
                    "severity": "info",
                    "path": "src/other.py",
                    "location": {"start_line": 9, "end_line": None, "symbol": None},
                    "message": "Single line.",
                    "evidence": {},
                },
            ],
        )
        for finding in document["findings"]:
            self.assertEqual(
                list(finding), ["rule_id", "severity", "path", "location", "message", "evidence"]
            )
        self.assertEqual(
            list(document["findings"][0]["evidence"]), ["actual_lines", "maximum_lines"]
        )

    def test_deterministic_bytes(self):
        self.assertEqual(render_json(FINDINGS), render_json(FINDINGS))

    def test_repository_finding_path_is_null(self):
        document = json.loads(render_json((REPOSITORY_FINDING,)))
        self.assertEqual(
            document["findings"],
            [
                {
                    "rule_id": "REPO_RULE",
                    "severity": "info",
                    "path": None,
                    "location": None,
                    "message": "Repository-level fact.",
                    "evidence": {"count": 2},
                }
            ],
        )
        self.assertIn('"path": null', render_json((REPOSITORY_FINDING,)))

    def test_nested_evidence_is_recursively_sorted_and_byte_identical(self):
        rendered = render_json((NESTED_FINDING,))
        evidence = json.loads(rendered)["findings"][0]["evidence"]
        self.assertEqual(
            evidence,
            {"alpha": {"x": "text", "y": [2, {"k1": 1.5, "k2": None}]}, "zeta": ["b.py", "a.py"]},
        )
        self.assertEqual(list(evidence), ["alpha", "zeta"])
        self.assertEqual(list(evidence["alpha"]), ["x", "y"])
        self.assertEqual(list(evidence["alpha"]["y"][1]), ["k1", "k2"])
        # Same content built with a different key insertion order renders identically.
        reordered = Finding(
            rule_id="NESTED_RULE",
            severity=Severity.WARNING,
            message="Nested evidence.",
            path="src/app.py",
            evidence={
                "alpha": {"x": "text", "y": [2, {"k1": 1.5, "k2": None}]},
                "zeta": ["b.py", "a.py"],
            },
        )
        self.assertEqual(rendered, render_json((NESTED_FINDING,)))
        self.assertEqual(rendered, render_json((reordered,)))

    def test_mixed_scope_order_matches_text(self):
        findings = sort_findings([NESTED_FINDING, FINDINGS[0], REPOSITORY_FINDING])
        json_paths = [f["path"] for f in json.loads(render_json(findings))["findings"]]
        self.assertEqual(json_paths, [None, "src/app.py", "src/big.py"])
        text_heads = [line.split(":", 1)[0] for line in render_text(findings).splitlines()
                      if not line.startswith(" ") and ": " in line]
        self.assertEqual(text_heads, ["<repository>", "src/app.py", "src/big.py"])

    def test_same_order_as_text(self):
        text_lines = [
            line for line in render_text(FINDINGS).splitlines() if line.startswith("src/")
        ]
        json_paths = [f["path"] for f in json.loads(render_json(FINDINGS))["findings"]]
        self.assertEqual([line.split(":", 1)[0] for line in text_lines], json_paths)


if __name__ == "__main__":
    unittest.main()
