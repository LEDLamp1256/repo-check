import tempfile
import unittest
from pathlib import Path

from repo_check.config import parse_config
from repo_check.discovery import discover
from repo_check.engine import run_rules
from repo_check.model import Finding, Location, Severity
from repo_check.rules.common.todo_comment import TodoComment

from support import write_tree


def todo(path: str, line: int, marker: str, text: str) -> Finding:
    return Finding(
        rule_id="TODO_COMMENT",
        severity=Severity.INFO,
        message=f"Comment contains a {marker} marker.",
        path=path,
        location=Location(start_line=line),
        evidence={"marker": marker, "text": text},
    )


class TodoCommentTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def findings(self, files, config=None):
        # A fresh directory per call so subtests do not see each other's files.
        root = Path(tempfile.mkdtemp(dir=self.root))
        write_tree(root, files)
        snapshot = discover(root, config or parse_config({}))
        return list(run_rules(snapshot, [TodoComment()]))

    def markers(self, name, source):
        """(line, marker, text) triples found in a single file."""
        return [
            (f.location.start_line, f.evidence["marker"], f.evidence["text"])
            for f in self.findings({name: source})
        ]


class MetadataTests(unittest.TestCase):
    def test_metadata(self):
        metadata = TodoComment.metadata
        self.assertEqual(metadata.id, "TODO_COMMENT")
        self.assertEqual(metadata.default_severity, Severity.INFO)
        self.assertTrue(metadata.title and metadata.description and metadata.remediation)


class PositiveTests(TodoCommentTestCase):
    def test_exact_findings_python(self):
        source = (
            "import os\n"
            "# TODO: handle errors\n"
            "x = 1  # FIXME(alice) off by one\n"
            "    ## TODO\n"
        )
        self.assertEqual(
            self.findings({"src/app.py": source}),
            [
                todo("src/app.py", 2, "TODO", "TODO: handle errors"),
                todo("src/app.py", 3, "FIXME", "FIXME(alice) off by one"),
                todo("src/app.py", 4, "TODO", "TODO"),
            ],
        )

    def test_hash_languages(self):
        for name in ("run.sh", "tool.rb", "lib.pl", "mix.exs", "stub.pyi"):
            with self.subTest(name=name):
                self.assertEqual(self.markers(name, "a\n# TODO fix\n"), [(2, "TODO", "TODO fix")])

    def test_slash_languages(self):
        for name in ("main.c", "App.swift", "Main.java", "lib.rs", "main.go", "app.ts",
                     "view.tsx", "a.kt", "a.cs", "a.dart", "a.mm", "a.cpp"):
            with self.subTest(name=name):
                self.assertEqual(
                    self.markers(name, "int a;\n// TODO: fix\n/// FIXME doc\n"),
                    [(2, "TODO", "TODO: fix"), (3, "FIXME", "FIXME doc")],
                )

    def test_trailing_slash_comment(self):
        self.assertEqual(
            self.markers("a.swift", "let x = 1 // TODO: rename\n"), [(1, "TODO", "TODO: rename")]
        )

    def test_block_comments(self):
        source = (
            "/* TODO: one-line block */\n"
            "/**\n"
            " * Docs.\n"
            " * FIXME: inside block\n"
            " TODO without star\n"
            " */\n"
            "int x; /* TODO trailing block */\n"
            "/*\n"
            "TODO: closes here */ int y;\n"
        )
        self.assertEqual(
            self.markers("a.c", source),
            [
                (1, "TODO", "TODO: one-line block"),
                (4, "FIXME", "FIXME: inside block"),
                (5, "TODO", "TODO without star"),
                (7, "TODO", "TODO trailing block"),
                (9, "TODO", "TODO: closes here"),
            ],
        )

    def test_test_files_are_checked(self):
        self.assertEqual(
            self.findings({"tests/test_a.py": "# TODO: cover edge case\n"}),
            [todo("tests/test_a.py", 1, "TODO", "TODO: cover edge case")],
        )

    def test_long_text_is_truncated_deterministically(self):
        text = "TODO " + "x" * 300
        [(line, marker, evidence_text)] = self.markers("a.py", f"# {text}\n")
        self.assertEqual(len(evidence_text), 120)
        self.assertTrue(evidence_text.endswith("..."))

    def test_crlf_and_bom(self):
        self.assertEqual(
            self.markers("a.py", "﻿x = 1\r\n# TODO: crlf\r\n".encode()),
            [(2, "TODO", "TODO: crlf")],
        )


class NegativeTests(TodoCommentTestCase):
    def test_prose_mentions_and_identifiers(self):
        source = (
            "# see the TODO list\n"
            "# Note: TODO later\n"
            "TODO_ITEMS = []\n"
            "# TODOS are tracked elsewhere\n"
            "# todo: lowercase\n"
            "# FIXMEs\n"
            "def todo(): pass\n"
        )
        self.assertEqual(self.markers("a.py", source), [])

    def test_markers_inside_strings(self):
        source = (
            'a = "# TODO: not a comment"\n'
            "b = '# FIXME: nor this'\n"
            'c = "say \\"hi\\" # TODO in string"\n'
            'url = "http://example.com//TODO"\n'
        )
        self.assertEqual(self.markers("a.py", source), [])

    def test_comment_token_must_follow_whitespace(self):
        self.assertEqual(self.markers("a.sh", 'echo ${#TODO}\nx=a#TODO\n'), [])
        self.assertEqual(
            self.markers("a.js", 'const u = "https://x.io";//TODO\nfetch("a//TODO")\n'), []
        )

    def test_python_docstrings_are_skipped(self):
        source = (
            'def f():\n'
            '    """Docstring.\n'
            '    # TODO: inside a docstring\n'
            '    """\n'
            "    s = '''\n"
            "    // TODO also string\n"
            "    # FIXME also string\n"
            "    '''\n"
            "    # TODO: real\n"
        )
        self.assertEqual(self.markers("a.py", source), [(9, "TODO", "TODO: real")])

    def test_multiline_strings_in_other_languages(self):
        swift = 'let s = """\n// TODO: text\n"""\n// TODO: real\n'
        self.assertEqual(self.markers("a.swift", swift), [(4, "TODO", "TODO: real")])
        js = "const t = `\n// TODO: template\n`;\n// FIXME: real\n"
        self.assertEqual(self.markers("a.js", js), [(4, "FIXME", "FIXME: real")])

    def test_hash_is_not_a_comment_in_c_like_languages(self):
        self.assertEqual(self.markers("a.c", "#define TODO 1\n# TODO\n"), [])

    def test_non_code_files_are_not_checked(self):
        files = {
            "README.md": "# TODO: docs\n",
            "config.yaml": "# TODO: config\n",
            "notes.txt": "# TODO: notes\n",
            "Makefile": "# TODO: make\n",
            "build/gen.py": "# TODO: build artifact\n",
            "static/app.min.js": "// TODO: generated\n",
            "gen/api.py": "# @generated\n# TODO: generated header\n",
            "data.py": b"\0# TODO: binary\n",
        }
        self.assertEqual(self.findings(files), [])

    def test_unknown_comment_syntax_is_not_checked(self):
        self.assertEqual(self.markers("a.lua", "-- TODO: lua\n"), [])
        self.assertEqual(self.markers("a.hs", "-- TODO: haskell\n"), [])

    def test_disabled(self):
        config = parse_config({"repo_check": {"rules": {"TODO_COMMENT": {"enabled": False}}}})
        self.assertEqual(self.findings({"a.py": "# TODO: x\n"}, config), [])

    def test_excluded(self):
        config = parse_config({"repo_check": {"exclude": ["vendor/"]}})
        self.assertEqual(self.findings({"vendor/a.py": "# TODO: x\n"}, config), [])


class DeterminismTests(TodoCommentTestCase):
    def test_ordering_across_files(self):
        files = {"b.py": "# TODO: b\n", "a/z.py": "x\n# FIXME: z\n", "a.py": "# TODO: a\n"}
        first = self.findings(files)
        self.assertEqual(
            [(f.path, f.location.start_line) for f in first],
            [("a.py", 1), ("a/z.py", 2), ("b.py", 1)],
        )
        self.assertEqual(first, self.findings(files))


if __name__ == "__main__":
    unittest.main()
