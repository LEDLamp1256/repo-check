import os
import tempfile
import time
import unittest
from pathlib import Path

from repo_check.config import parse_config
from repo_check.discovery import discover
from repo_check.engine import run_rules
from repo_check.model import Finding, Location, Severity
from repo_check.rules.common.broken_local_doc_link import (
    BrokenLocalDocLink,
    extract_link_targets,
    resolve_target,
)

from support import write_tree

# The rule is disabled by default; these tests exercise it explicitly enabled.
ENABLED = {"rules": {"BROKEN_LOCAL_DOC_LINK": {"enabled": True}}}


def enabled_config(**section):
    return parse_config({"repo_check": {**ENABLED, **section}})


def broken(path: str, line: int, link: str, resolved: str, **extra: str) -> Finding:
    return Finding(
        rule_id="BROKEN_LOCAL_DOC_LINK",
        severity=Severity.WARNING,
        message=f"Local link target does not exist: {link}",
        path=path,
        location=Location(start_line=line),
        evidence={"link": link, "resolved_path": resolved, **extra},
    )


class MetadataTests(unittest.TestCase):
    def test_metadata(self):
        metadata = BrokenLocalDocLink.metadata
        self.assertEqual(metadata.id, "BROKEN_LOCAL_DOC_LINK")
        self.assertEqual(metadata.default_severity, Severity.WARNING)
        self.assertTrue(metadata.title and metadata.description and metadata.remediation)


class ExtractTests(unittest.TestCase):
    def targets(self, text):
        return list(extract_link_targets(tuple(text.split("\n"))))

    def test_inline_links_images_and_references(self):
        text = (
            "See [guide](docs/guide.md) and ![logo](img/logo.png \"Logo\").\n"
            "Two [a](a.md) [b](<b c.md>) on one line.\n"
            "[ref]: docs/ref.md\n"
            "   [spaced]: <docs/spaced ref.md> \"title\"\n"
            "[![badge](badge.svg)](docs/badge-target.md)\n"
            "Parens [p](docs/file_(1).md) ok.\n"
        )
        self.assertEqual(
            self.targets(text),
            [
                (1, "docs/guide.md"), (1, "img/logo.png"),
                (2, "a.md"), (2, "b c.md"),
                (3, "docs/ref.md"),
                (4, "docs/spaced ref.md"),
                (5, "badge.svg"), (5, "docs/badge-target.md"),
                (6, "docs/file_(1).md"),
            ],
        )

    def test_code_and_comments_are_ignored(self):
        text = (
            "```python\n"
            "x = [a](in-fence.md)\n"
            "```\n"
            "~~~~\n"
            "[b](tilde-fence.md)\n"
            "~~~\n"
            "still in fence [c](still.md)\n"
            "~~~~\n"
            "Inline `[d](in-code.md)` and ``[e](double.md)``.\n"
            "<!-- [f](comment.md) --> [g](after-comment.md)\n"
            "<!--\n"
            "[h](multi-comment.md)\n"
            "-->\n"
            "[i](real.md)\n"
        )
        self.assertEqual(self.targets(text), [(10, "after-comment.md"), (14, "real.md")])

    def test_indented_code_is_ignored(self):
        text = (
            "Example:\n"
            "\n"
            "    [x](missing.md)\n"
            "\t[tab](tab-missing.md)\n"
            "        ![deep](deep.png)\n"
            "   [three](three-spaces.md)\n"
            "[plain](plain.md)\n"
        )
        self.assertEqual(self.targets(text), [(6, "three-spaces.md"), (7, "plain.md")])

    def test_indented_comment_close_still_ends_comment(self):
        text = "<!--\n    -->\n[after](after.md)\n"
        self.assertEqual(self.targets(text), [(3, "after.md")])

    def test_html_comment_boundaries(self):
        text = (
            "<!-- a --> [x](x.md) <!-- b --> [y](y.md) <!-- open [z](z.md)\n"  # 1
            "[hidden](hidden.md) --> [w](w.md)\n"                            # 2
            "<!--> [still](inside.md) --> [v](v.md)\n"                       # 3
            "<!---> [still2](inside2.md) --> [u](u.md)\n"                    # 4
        )
        self.assertEqual(
            self.targets(text),
            [(1, "x.md"), (1, "y.md"), (2, "w.md"), (3, "v.md"), (4, "u.md")],
        )

    def test_many_unclosed_comment_openers_stay_linear(self):
        # Each unclosed "<!--" used to rescan the rest of the line
        # (quadratic: ~8 s for 20k openers).
        line = "[a](a.md) " + "<!--" * 200_000
        started = time.perf_counter()
        self.assertEqual(self.targets(line), [(1, "a.md")])
        self.assertLess(time.perf_counter() - started, 10.0)

    def test_non_links(self):
        text = (
            "arr[i](x) is code-like\n"
            "\\[escaped](nope.md)\n"
            "[^1]: footnote text\n"
            "[text] (spaced.md)\n"
        )
        self.assertEqual(self.targets(text), [])


class ResolveTests(unittest.TestCase):
    def test_skipped_targets(self):
        for target in ("", "#anchor", "https://x.io/a.md", "mailto:a@b.c", "//cdn/x.js",
                       "/abs/path.md", "{{ site.url }}/x", "$VAR/x.md", "docs\\x.md",
                       "C:/x.md", "?query", "../outside.md", "../../x.md",
                       "sponsors@example.org", "a.b@c.d"):
            with self.subTest(target=target):
                self.assertIsNone(resolve_target("", target))

    def test_resolution(self):
        cases = {
            ("", "docs/a.md"): "docs/a.md",
            ("", "./docs/a.md#section"): "docs/a.md",
            ("", "docs/a.md?raw=1"): "docs/a.md",
            ("docs", "../README.md"): "README.md",
            ("docs/sub", "../other.md"): "docs/other.md",
            ("docs", "."): "docs",
            ("docs", ".."): "",
            ("", "my%20file.md"): "my file.md",
            ("", "docs/"): "docs",
            ("", "node_modules/@scope/pkg/README.md"): "node_modules/@scope/pkg/README.md",
            ("", "pkg@1.0/README.md"): "pkg@1.0/README.md",
        }
        for (base, target), expected in cases.items():
            with self.subTest(base=base, target=target):
                self.assertEqual(resolve_target(base, target), expected)
        self.assertIsNone(resolve_target("docs", "../../up.md"))


class BrokenLocalDocLinkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def findings(self, config=None):
        snapshot = discover(self.root, config or enabled_config())
        return list(run_rules(snapshot, (), [BrokenLocalDocLink()]))

    def test_exact_findings(self):
        write_tree(
            self.root,
            {
                "README.md": (
                    "# Project\n"
                    "[Guide](docs/guide.md) [Missing](docs/missing.md)\n"
                    "![Img](img/none.png)\n"
                    "[ref]: nowhere/ref.md\n"
                ),
                "docs/guide.md": "Back to [readme](../README.md), [gone](./gone.md#top).\n",
            }
        )
        self.assertEqual(
            self.findings(),
            [
                broken("README.md", 2, "docs/missing.md", "docs/missing.md"),
                broken("README.md", 3, "img/none.png", "img/none.png"),
                broken("README.md", 4, "nowhere/ref.md", "nowhere/ref.md"),
                broken("docs/guide.md", 1, "./gone.md#top", "docs/gone.md"),
            ],
        )

    def test_existing_targets(self):
        write_tree(
            self.root,
            {
                "README.md": (
                    "[f](src/app.py) [d](docs) [d2](docs/) [root](.) [self](README.md)\n"
                    "[anchor](docs/guide.md#install) [space](my%20notes.md) [a](<my notes.md>)\n"
                    "[ext](https://example.com/missing.md) [top](#top) [abs](/nope.md)\n"
                ),
                "src/app.py": "",
                "docs/guide.md": "",
                "my notes.md": "",
            },
        )
        self.assertEqual(self.findings(), [])

    def test_unindexed_targets_are_not_reported(self):
        write_tree(
            self.root,
            {
                "README.md": (
                    "[pruned](node_modules/pkg/README.md) [cache](__pycache__/x.pyc)\n"
                    "[excluded](vendor/lib.md) [excluded-file](private.md)\n"
                    "[under-excluded](vendor/does/not/exist.md) [git](.git/config)\n"
                ),
                "node_modules/pkg/README.md": "",
                "__pycache__/x.pyc": b"\0",
                ".git/config": "",
                "vendor/lib.md": "",
                "private.md": "",
            },
        )
        config = enabled_config(exclude=["vendor/", "private.md"])
        self.assertEqual(self.findings(config), [])

    def test_missing_target_under_absent_pruned_name_is_reported(self):
        # Pruning only hides what exists; a directory that is not there at all
        # is simply missing.
        write_tree(self.root, {"README.md": "[c](__pycache__/x.pyc)\n"})
        self.assertEqual(
            self.findings(), [broken("README.md", 1, "__pycache__/x.pyc", "__pycache__/x.pyc")]
        )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlink_targets_are_not_reported(self):
        write_tree(self.root, {"README.md": "[l](link.md) [d](linkdir/x.md)\n", "real.md": ""})
        try:
            os.symlink(self.root / "real.md", self.root / "link.md")
            os.symlink(self.root, self.root / "linkdir", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"cannot create symlink: {exc}")
        self.assertEqual(self.findings(), [])

    def test_indented_code_link_ignored_but_plain_link_reported(self):
        write_tree(
            self.root,
            {"README.md": "Usage:\n\n    [x](missing.md)\n\nSee [x](missing.md).\n"},
        )
        self.assertEqual(self.findings(), [broken("README.md", 5, "missing.md", "missing.md")])

    def test_documentation_site_links(self):
        write_tree(
            self.root,
            {
                "docs/commands/npm-diff.md": (
                    "[npm-outdated](npm-outdated) [cfg](npm-config#section)\n"
                    "[html](npm-outdated.html) [htm](npm-config.htm)\n"
                    "[md-ext](other.markdown)\n"
                    "[missing](npm-nope) [missing-html](npm-nope.html) [txt](npm-outdated.txt)\n"
                ),
                "docs/commands/npm-outdated.md": "",
                "docs/commands/npm-config.markdown": "",
                "docs/commands/other.markdown": "",
            },
        )
        page = "docs/commands/npm-diff.md"
        self.assertEqual(
            self.findings(),
            [
                broken(page, 4, "npm-nope", "docs/commands/npm-nope"),
                broken(page, 4, "npm-nope.html", "docs/commands/npm-nope.html"),
                broken(page, 4, "npm-outdated.txt", "docs/commands/npm-outdated.txt"),
            ],
        )

    def test_case_mismatch_is_reported_with_evidence(self):
        write_tree(self.root, {"README.md": "[g](docs/Guide.md)\n", "docs/guide.md": ""})
        self.assertEqual(
            self.findings(),
            [broken("README.md", 1, "docs/Guide.md", "docs/Guide.md",
                    case_insensitive_match="docs/guide.md")],
        )

    def test_links_above_analysis_root_are_not_reported(self):
        write_tree(self.root, {"pkg/README.md": "[up](../../outside.md) [top](../x.md)\n"})
        snapshot = discover(self.root / "pkg", enabled_config())
        self.assertEqual(list(run_rules(snapshot, (), [BrokenLocalDocLink()])), [])

    def test_only_markdown_documentation_is_checked(self):
        write_tree(
            self.root,
            {
                "docs/index.rst": "`x <missing.rst>`_\n",
                "notes.txt": "[a](missing.md)\n",
                "src/app.py": "# [a](missing.md)\n",
                "build/out.md": "[a](missing.md)\n",
                "CHANGELOG.markdown": "[a](missing-changelog.md)\n",
            },
        )
        self.assertEqual(
            self.findings(),
            [broken("CHANGELOG.markdown", 1, "missing-changelog.md", "missing-changelog.md")],
        )

    def test_docc_catalog_documents_are_not_checked(self):
        # DocC resolves images by file name anywhere in the catalog and turns
        # relative links into hosted documentation URLs, so file-system
        # resolution does not apply inside a .docc catalog (swift-nio,
        # swift-argument-parser). Links into a catalog from elsewhere still are.
        write_tree(
            self.root,
            {
                "Sources/Lib/Documentation.docc/Lib.md": (
                    "![shot](repeat.png)\n[core]: ./NIOCore\n"
                ),
                "Sources/Lib/Documentation.docc/Images/repeat.png": b"\x89PNG\0",
                "Sources/Lib/Documentation.docc/Articles/Guide.md": "[x](missing.md)\n",
                "Sources/Lib/Other.DocC/Page.md": "[x](missing.md)\n",
                "Sources/Lib/docc/Page.md": "[x](missing-plain.md)\n",
                "README.md": (
                    "[ok](Sources/Lib/Documentation.docc/Lib.md) "
                    "[bad](Sources/Lib/Documentation.docc/Gone.md)\n"
                ),
            },
        )
        self.assertEqual(
            self.findings(),
            [
                broken("README.md", 1, "Sources/Lib/Documentation.docc/Gone.md",
                       "Sources/Lib/Documentation.docc/Gone.md"),
                broken("Sources/Lib/docc/Page.md", 1, "missing-plain.md",
                       "Sources/Lib/docc/missing-plain.md"),
            ],
        )

    def test_duplicate_link_on_same_line_reported_once(self):
        write_tree(self.root, {"README.md": "[a](x.md) and [again](x.md)\n[b](x.md)\n"})
        self.assertEqual(
            self.findings(),
            [broken("README.md", 1, "x.md", "x.md"), broken("README.md", 2, "x.md", "x.md")],
        )

    def test_disabled(self):
        write_tree(self.root, {"README.md": "[a](missing.md)\n"})
        config = parse_config(
            {"repo_check": {"rules": {"BROKEN_LOCAL_DOC_LINK": {"enabled": False}}}}
        )
        self.assertEqual(self.findings(config), [])

    def test_excluded_document_is_not_checked(self):
        write_tree(self.root, {"docs/gen/api.md": "[a](missing.md)\n"})
        config = enabled_config(exclude=["docs/gen/"])
        self.assertEqual(self.findings(config), [])

    def test_deterministic(self):
        write_tree(
            self.root,
            {"b.md": "[x](1.md)\n", "a/z.md": "[y](2.md)\n", "a.md": "[z](3.md)\n[w](4.md)\n"},
        )
        first = self.findings()
        self.assertEqual(
            [(f.path, f.location.start_line) for f in first],
            [("a.md", 1), ("a.md", 2), ("a/z.md", 1), ("b.md", 1)],
        )
        self.assertEqual(first, self.findings())


if __name__ == "__main__":
    unittest.main()
