import unittest

from repo_check.languages.swift import SwiftOperatorUse, analyze_swift


def uses(source: str):
    facts = analyze_swift(tuple(source.split("\n")))
    return [(u.operator, u.line, u.column) for u in facts.operator_uses]


class OperatorRecognitionTests(unittest.TestCase):
    def test_start_middle_and_end_of_line(self):
        source = (
            "try! run()\n"                       # start
            "let v = f(x as! Int) + 1\n"          # middle
            "let w = try! g()\n"                  # before call
            "let z = value as! String\n"          # end
        )
        self.assertEqual(
            uses(source),
            [("try!", 1, 1), ("as!", 2, 13), ("try!", 3, 9), ("as!", 4, 15)],
        )

    def test_multiple_and_both_on_one_line(self):
        source = "let a = try! load() as! [String: Any]; let b = try! c() as! D\n"
        self.assertEqual(
            uses(source),
            [("try!", 1, 9), ("as!", 1, 21), ("try!", 1, 48), ("as!", 1, 57)],
        )

    def test_fact_values(self):
        facts = analyze_swift(("  let x = y as! Z",))
        self.assertEqual(facts.operator_uses, (SwiftOperatorUse("as!", 1, 13),))

    def test_non_forced_forms_are_not_reported(self):
        source = (
            "let a = try f()\n"
            "let b = try? f()\n"
            "let c = x as Int\n"
            "let d = x as? Int\n"
            "if try await ok() {}\n"
        )
        self.assertEqual(uses(source), [])

    def test_bang_must_be_left_bound(self):
        # Only a "!" immediately after the keyword is the forced operator.
        # "try !x" applies try to the logical not of x; "as !T" is not a cast.
        self.assertEqual(uses("let a = try !flag()\nlet b = x as !Int\nlet c = try\t!d\n"), [])
        # A right-bound "!" is still the operator: try!f() and as!Int.
        self.assertEqual(uses("let a = try!f()\nlet b = x as!Int\n"),
                         [("try!", 1, 9), ("as!", 2, 11)])

    def test_greedy_operators_are_not_forced_operators(self):
        # "!=" and "!!" are single operator tokens in Swift.
        self.assertEqual(uses("if try!= x {}\nlet y = a as!! b\nlet z = try!=y\n"), [])

    def test_comment_right_after_bang_ends_the_operator(self):
        self.assertEqual(uses("let a = try!// note\nlet b = x as!/* c */ Int\n"),
                         [("try!", 1, 9), ("as!", 2, 11)])

    def test_identifier_near_misses(self):
        source = (
            "retry!\n"                  # longer identifier
            "tryAgain! ()\n"
            "let has! = 1\n"
            "canvas!.draw()\n"
            "x.try!.run()\n"            # member named try
            "self.as!\n"
            "`try`!()\n"                # escaped identifier
            "`as`!.f\n"
            "@as!\n"
            "#try!\n"
            "$as!\n"
            "try_! ()\n"
            "as2!\n"
        )
        self.assertEqual(uses(source), [])

    def test_unicode_identifiers_are_single_tokens(self):
        self.assertEqual(uses("let éas! = 1\nlet tryé! = 2\n"), [])


class CommentTests(unittest.TestCase):
    def test_line_comments(self):
        source = (
            "// try! at start of comment line\n"
            "let a = b // then as! in trailing comment\n"
            "    /// doc: try! as!\n"
            "let c = try! d() // and try! again\n"
        )
        self.assertEqual(uses(source), [("try!", 4, 9)])

    def test_block_and_nested_block_comments(self):
        source = (
            "/* try! */ let a = 1\n"
            "/* outer /* inner as! */ still comment try! */ let b = try! c()\n"
            "/*\n"
            " * multi-line: try!\n"
            " * /* nested\n"
            " *    as! */\n"
            " * still inside try!\n"
            " */\n"
            "let d = e as! F\n"
        )
        self.assertEqual(uses(source), [("try!", 2, 56), ("as!", 9, 11)])

    def test_unterminated_block_comment_hides_rest_of_file(self):
        self.assertEqual(uses("let a = try! b()\n/* open\ntry! c()\n"), [("try!", 1, 9)])

    def test_slash_star_inside_string_is_not_a_comment(self):
        self.assertEqual(uses('let p = "/*"\nlet a = try! b()\nlet q = "*/"\n'),
                         [("try!", 2, 9)])


class StringTests(unittest.TestCase):
    def test_ordinary_strings_and_escaped_quotes(self):
        source = (
            'let a = "try! and as!"\n'
            'let b = "escaped \\" try! still in string"\n'
            'let c = "backslash \\\\" ; let d = try! e()\n'
            'let f = "// not a comment" ; let g = h as! I\n'
        )
        self.assertEqual(uses(source), [("try!", 3, 34), ("as!", 4, 40)])

    def test_multiline_strings(self):
        source = (
            'let text = """\n'
            "    try! in text\n"
            '    "quoted" and ""double"" as!\n'
            '    escaped \\""" still text try!\n'
            '    """\n'
            "let a = try! b()\n"
        )
        self.assertEqual(uses(source), [("try!", 6, 9)])

    def test_raw_strings(self):
        source = (
            'let a = #"raw "try!" \\n as!"#\n'
            'let b = ##"has "# inside try! "##\n'
            'let c = #"""\n'
            '    """ not the end try!\n'
            '    """#\n'
            'let d = ##"""\n'
            '    """# not the end as!\n'
            '    """##\n'
            "let e = try! f()\n"
        )
        self.assertEqual(uses(source), [("try!", 9, 9)])

    def test_interpolation_is_skipped_with_the_string(self):
        source = (
            'let a = "\\(try! x())"\n'
            'let b = "\\(dict["key"] as! Int) try!"\n'
            'let c = "\\(f("nested \\(g(")"))")) as!"\n'
            'let d = #"\\#(x as! Y) \\(not interpolation) try!"#\n'
            'let e = """\n'
            '    \\(try! z(")"))\n'
            '    """\n'
            "let f = try! g()\n"
        )
        self.assertEqual(uses(source), [("try!", 8, 9)])

    def test_unterminated_single_line_string_recovers_at_line_end(self):
        self.assertEqual(uses('let a = "open try!\nlet b = try! c()\n'), [("try!", 2, 9)])

    def test_extended_regex_literal(self):
        self.assertEqual(uses('let r = #/"try!" as!/#\nlet a = try! b()\n'), [("try!", 2, 9)])

    def test_other_hash_syntax_is_code(self):
        source = "#if DEBUG\nlet a = try! b()\n#endif\nlet s = #selector(f)\nlet c = d as! E\n"
        self.assertEqual(uses(source), [("try!", 2, 9), ("as!", 5, 11)])


class BareRegexTests(unittest.TestCase):
    """Conservative policy for bare /.../ regex literals (no regex parser)."""

    def test_regex_contents_are_not_reported(self):
        self.assertEqual(uses("let r = /try! x/\n"), [])
        self.assertEqual(uses("/as! Foo/\n"), [])
        self.assertEqual(uses("let rs = [/try! a/, /as! b/]\nf(/try! c/)\n"), [])

    def test_regex_with_quotes_does_not_desynchronize(self):
        self.assertEqual(uses('let r = /"try!"/\n'), [])
        self.assertEqual(uses('let r = /"/; let s = "try! in a real string"\n'), [])
        self.assertEqual(uses("let r = /as!\\/\"x/\n"), [])  # escaped slash inside

    def test_code_after_a_confident_regex_is_still_scanned(self):
        # Expression-start "/" not followed by whitespace cannot be division,
        # so the regex boundary is known and later code on the line is found.
        self.assertEqual(uses('let r = /"try!"/; let a = try! b()\n'), [("try!", 1, 27)])
        self.assertEqual(uses('let r = /"/; let a = try! b()\n'), [("try!", 1, 22)])
        self.assertEqual(uses("let r = /as!|try!/; let a = x as! Y\n"), [("as!", 1, 31)])

    def test_ambiguous_slash_regions_are_suppressed(self):
        # After an operand, "/" may be division or a regex: never report what
        # lies between two slashes (false negatives are accepted).
        self.assertEqual(uses("x = y /try! z/\n"), [])
        self.assertEqual(uses("let q = a / b; let c = try! d() / e\n"), [])
        self.assertEqual(uses("let q = f() /as! T/ 2\n"), [])

    def test_ambiguous_region_that_could_change_state_suppresses_rest_of_line(self):
        source = 'let x = a / 2; print("try!/"); let y = try! z()\nlet w = try! v()\n'
        self.assertEqual(uses(source), [("try!", 2, 9)])

    def test_single_division_is_ordinary_code(self):
        self.assertEqual(uses("let h = total / 2; let c = try! d()\n"), [("try!", 1, 28)])
        self.assertEqual(uses("let h = total / 2\nlet c = x as! Y / 3\n"), [("as!", 2, 11)])

    def test_space_after_slash_is_not_a_confident_regex(self):
        self.assertEqual(uses("let r = / try! x/\n"), [])

    def test_extended_regex_behavior_is_unchanged(self):
        self.assertEqual(uses('let r = #/"try!" as!/#\nlet a = try! b()\n'), [("try!", 2, 9)])
        self.assertEqual(uses("let r = ##/try! /# as!/##; let a = x as! Y\n"), [("as!", 1, 38)])

    def test_comments_are_unchanged(self):
        source = (
            "let a = b // try! /x/ as!\n"
            "/* /try!/ */ let c = try! d()\n"
            "let e = f /* / */ as! G\n"
        )
        self.assertEqual(uses(source), [("try!", 2, 22), ("as!", 3, 19)])


class UnicodeBoundaryTests(unittest.TestCase):
    """Keywords directly touching non-ASCII characters are never reported."""

    def test_non_ascii_before_keyword(self):
        for prefix in ("😀", "é", "a\u0301", "\u00a0", "→", "日本"):
            with self.subTest(prefix=prefix):
                self.assertEqual(uses(f"let v = {prefix}try! f()\nlet w = {prefix}as! T\n"), [])

    def test_non_ascii_between_keyword_and_bang(self):
        for suffix in ("😀", "é", "\u0301", "\u200d"):
            with self.subTest(suffix=suffix):
                self.assertEqual(uses(f"let v = try{suffix}! f()\nlet w = x as{suffix}! T\n"), [])

    def test_whitespace_separated_unicode_is_unaffected(self):
        source = "let é = try! f()\nlet 😀 = x as! Y\nlet s = \"é\" + (try! g())\n"
        self.assertEqual(uses(source), [("try!", 1, 9), ("as!", 2, 11), ("try!", 3, 16)])


class RobustnessTests(unittest.TestCase):
    def test_empty_and_plain(self):
        self.assertEqual(uses(""), [])
        self.assertEqual(uses("import Foundation\n"), [])

    def test_deterministic(self):
        source = "let a = try! b() as! C\n/* x */ let d = try! e()\n"
        self.assertEqual(uses(source), uses(source))

    def test_pathological_interpolation_nesting_does_not_crash(self):
        depth = 5000
        source = "let a = " + '"\\(' * depth + ")\"" * depth + "\nlet b = try! c()\n"
        facts = analyze_swift(tuple(source.split("\n")))
        self.assertIsNone(facts)


if __name__ == "__main__":
    unittest.main()
