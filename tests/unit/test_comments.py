import time
import unittest

from repo_check.languages.comments import (
    CommentMarker,
    extract_comment_markers,
    has_comment_syntax,
)

MARKERS = ("TODO", "FIXME")


def extract(path, text, markers=MARKERS):
    return list(extract_comment_markers(path, tuple(text.split("\n")), markers))


class CommentSyntaxTests(unittest.TestCase):
    def test_supported_extensions(self):
        for path in ("a.py", "b.pyi", "run.sh", "x.rb", "main.c", "App.swift", "Main.java",
                     "lib.rs", "main.go", "app.ts", "view.tsx", "a.dart", "dir/UPPER.PY"):
            with self.subTest(path=path):
                self.assertTrue(has_comment_syntax(path))

    def test_unsupported_extensions(self):
        for path in ("a.lua", "b.hs", "README.md", "config.yaml", "Makefile", "a.txt", "x"):
            with self.subTest(path=path):
                self.assertFalse(has_comment_syntax(path))
                self.assertEqual(extract(path, "# TODO: x\n// TODO: y\n-- TODO: z"), [])


class ExtractionTests(unittest.TestCase):
    def test_exact_marker_facts(self):
        text = "x = 1\n# TODO: first\ny = 2  # FIXME(bob) second\n"
        self.assertEqual(
            extract("a.py", text),
            [
                CommentMarker(line_number=2, marker="TODO", text="TODO: first"),
                CommentMarker(line_number=3, marker="FIXME", text="FIXME(bob) second"),
            ],
        )

    def test_block_comment_terminator_is_removed(self):
        self.assertEqual(
            extract("a.c", "/* TODO: tidy */\n/*\n * FIXME: later */\n"),
            [
                CommentMarker(1, "TODO", "TODO: tidy"),
                CommentMarker(3, "FIXME", "FIXME: later"),
            ],
        )

    def test_text_is_not_truncated_by_the_extractor(self):
        long_text = "TODO " + "x" * 300
        [found] = extract("a.py", f"# {long_text}")
        self.assertEqual(found.text, long_text)

    def test_marker_set_is_a_parameter(self):
        text = "# XXX: hack\n# TODO: not requested\n# HACK here\n"
        self.assertEqual(
            extract("a.py", text, ("XXX", "HACK")),
            [CommentMarker(1, "XXX", "XXX: hack"), CommentMarker(3, "HACK", "HACK here")],
        )

    def test_marker_order_does_not_change_results(self):
        text = "# TODO a\n# FIXME b\n"
        self.assertEqual(extract("a.py", text, ("TODO", "FIXME")),
                         extract("a.py", text, ("FIXME", "TODO")))

    def test_invalid_marker_sets_are_rejected(self):
        for markers in ((), ("TO DO",), ("TODO|.*",), ("",)):
            with self.subTest(markers=markers), self.assertRaises(ValueError):
                extract("a.py", "# TODO\n", markers)

    def test_lexical_exclusions(self):
        text = (
            'a = "# TODO: in string"\n'
            "# see the TODO list\n"
            "TODO_ITEMS = []  # not a marker: TODO\n"
            '"""\n'
            "# TODO: inside docstring\n"
            '"""\n'
            "# TODO: real\n"
        )
        self.assertEqual(extract("a.py", text), [CommentMarker(7, "TODO", "TODO: real")])

    def test_accepts_any_line_sequence(self):
        lines = ["// TODO: list input"]
        self.assertEqual(
            list(extract_comment_markers("a.go", lines, MARKERS)),
            [CommentMarker(1, "TODO", "TODO: list input")],
        )

    def test_escapes_and_quote_parity_decide_comment_starts(self):
        text = (
            'a = "x \\" # TODO: in string" # TODO: after string\n'  # 1
            "b = 'it\\'s' # FIXME: escaped quote closed\n"         # 2
            'c = "\\\\" # TODO: escaped backslash closes\n'      # 3
            'd = "open # TODO: unterminated\n'                       # 4
        )
        self.assertEqual(
            extract("a.py", text),
            [
                CommentMarker(1, "TODO", "TODO: after string"),
                CommentMarker(2, "FIXME", "FIXME: escaped quote closed"),
                CommentMarker(3, "TODO", "TODO: escaped backslash closes"),
            ],
        )

    def test_many_comment_like_tokens_inside_a_string_stay_linear(self):
        # Every " #" / " //" sits inside a string; each used to re-scan the
        # line prefix (quadratic: ~2 s for 40k tokens, far longer for a large
        # bundled file). The line is now scanned once.
        count = 200_000
        for path, token in (("a.py", " #x"), ("a.js", " //x")):
            with self.subTest(path=path):
                line = '"' + token * count + '" # TODO: real'
                started = time.perf_counter()
                found = extract(path, line)
                elapsed = time.perf_counter() - started
                self.assertEqual(found, [CommentMarker(1, "TODO", "TODO: real")] if path == "a.py"
                                 else [])
                self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()
