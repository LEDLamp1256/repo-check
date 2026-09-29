import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from repo_check.config import default_config, parse_config
from repo_check.discovery import classify, discover, read_text_lines
from repo_check.errors import DiscoveryError
from repo_check.model import FileClassification as FC
from repo_check.model import PathKind

from support import FIXTURES, write_tree


def config_with_exclude(*patterns: str):
    return parse_config({"repo_check": {"exclude": list(patterns)}})


class DiscoveryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def paths(self, config=None):
        snapshot = discover(self.root, config or default_config())
        return [f.relative_path for f in snapshot.files]

    def file(self, relative, config=None):
        snapshot = discover(self.root, config or default_config())
        return next(f for f in snapshot.files if f.relative_path == relative)


class WalkTests(DiscoveryTestCase):
    def test_nested_files_with_posix_relative_paths(self):
        write_tree(self.root, {"a.py": "", "pkg/sub/deep.py": "", "pkg/mod.py": ""})
        self.assertEqual(self.paths(), ["a.py", "pkg/mod.py", "pkg/sub/deep.py"])

    def test_deterministic_ordering_is_path_string_order(self):
        # Plain code-point order: uppercase before "_" before lowercase, "." before
        # "/", and "C.py" before "b.py" (a case-insensitive sort would differ).
        # No two names differ only by case, so this also runs on case-insensitive
        # file systems.
        write_tree(
            self.root,
            {"b.py": "", "a/z.py": "", "a.txt": "", "C.py": "", "a/b/c.py": "", "_x.py": ""},
        )
        expected = ["C.py", "_x.py", "a.txt", "a/b/c.py", "a/z.py", "b.py"]
        self.assertEqual(self.paths(), expected)
        self.assertEqual(self.paths(), expected)

    def test_snapshot_contents(self):
        write_tree(self.root, {"src/app.py": "x = 1\ny = 2\n"})
        config = default_config()
        snapshot = discover(self.root, config)
        self.assertEqual(snapshot.root, self.root.resolve())
        self.assertIs(snapshot.config, config)
        (file,) = snapshot.files
        self.assertEqual(file.relative_path, "src/app.py")
        self.assertEqual(file.absolute_path, self.root.resolve() / "src" / "app.py")
        self.assertEqual(file.size_bytes, 12)
        self.assertFalse(file.is_binary)
        self.assertEqual(file.line_count, 2)
        self.assertEqual(file.classification, FC.SOURCE)

    def test_git_directory_and_git_file_are_ignored(self):
        write_tree(self.root, {".git/config": "[core]\n", ".git/objects/ab": b"\0", "a.py": ""})
        self.assertEqual(self.paths(), ["a.py"])
        nested = self.root / "sub"
        write_tree(nested, {".git": "gitdir: ../.git/worktrees/sub\n", "b.py": ""})
        self.assertEqual(self.paths(), ["a.py", "sub/b.py"])

    def test_cache_and_dependency_directories_are_pruned(self):
        write_tree(
            self.root,
            {
                "__pycache__/m.cpython-311.pyc": b"\0",
                "pkg/__pycache__/n.pyc": b"\0",
                ".mypy_cache/x.json": "{}",
                ".pytest_cache/v": "",
                "node_modules/lib/index.js": "",
                ".venv/lib/site.py": "",
                ".build/checkouts/dep.swift": "",
                "pkg/mod.py": "",
            },
        )
        self.assertEqual(self.paths(), ["pkg/mod.py"])

    def test_hidden_non_pruned_directories_are_included(self):
        write_tree(self.root, {".github/workflows/ci.yml": "on: push\n"})
        self.assertEqual(self.paths(), [".github/workflows/ci.yml"])

    def test_missing_root_is_an_error(self):
        with self.assertRaisesRegex(DiscoveryError, "does not exist"):
            discover(self.root / "missing", default_config())

    def test_file_root_is_an_error(self):
        write_tree(self.root, {"a.py": ""})
        with self.assertRaisesRegex(DiscoveryError, "not a directory"):
            discover(self.root / "a.py", default_config())


class ExclusionTests(DiscoveryTestCase):
    def setUp(self):
        super().setUp()
        write_tree(
            self.root,
            {
                "src/main.py": "",
                "vendor/lib.py": "",
                "src/vendor/inner.py": "",
                "static/app.js": "",
                "static/sub/deep.js": "",
                "gen/api_gen.py": "",
                "notes": "",
                "notes.py": "",
            },
        )

    def test_directory_pattern_excludes_subtree_at_root_only(self):
        self.assertEqual(
            self.paths(config_with_exclude("vendor/")),
            ["gen/api_gen.py", "notes", "notes.py", "src/main.py", "src/vendor/inner.py",
             "static/app.js", "static/sub/deep.js"],
        )

    def test_star_matches_across_directories(self):
        self.assertEqual(
            self.paths(config_with_exclude("static/*.js")),
            ["gen/api_gen.py", "notes", "notes.py", "src/main.py", "src/vendor/inner.py",
             "vendor/lib.py"],
        )

    def test_nested_directory_pattern(self):
        paths = self.paths(config_with_exclude("*/vendor/", "vendor/"))
        self.assertNotIn("vendor/lib.py", paths)
        self.assertNotIn("src/vendor/inner.py", paths)

    def test_trailing_slash_matches_directories_only(self):
        paths = self.paths(config_with_exclude("notes/"))
        self.assertIn("notes", paths)

    def test_plain_pattern_matches_file_or_directory(self):
        paths = self.paths(config_with_exclude("notes", "gen"))
        self.assertNotIn("notes", paths)
        self.assertNotIn("gen/api_gen.py", paths)
        self.assertIn("notes.py", paths)

    def test_excluded_unreadable_file_is_never_read(self):
        write_tree(self.root, {"secret/data.py": ""})
        target = self.root / "secret" / "data.py"
        os.chmod(target, 0)
        try:
            if os.access(target, os.R_OK):
                self.skipTest("running with privileges that bypass file permissions")
            with self.assertRaisesRegex(DiscoveryError, "cannot read file secret/data.py"):
                self.paths()
            self.assertNotIn("secret/data.py", self.paths(config_with_exclude("secret/")))
        finally:
            os.chmod(target, 0o644)

    def test_exclusions_fixture(self):
        snapshot = discover(
            FIXTURES / "exclusions", config_with_exclude("vendor/", "static/*.js",
                                                         "scripts/generated_*.py")
        )
        self.assertEqual(
            [f.relative_path for f in snapshot.files],
            ["repo-check.toml", "scripts/tool.py", "src/main.py"],
        )


class ContentTests(DiscoveryTestCase):
    def test_line_counting(self):
        write_tree(
            self.root,
            {
                "empty.py": "",
                "one_newline.py": "\n",
                "no_trailing.py": "a\nb",
                "trailing.py": "a\nb\n",
                "crlf.py": b"a\r\nb\r\n",
                "blank_lines.py": "a\n\n\n",
            },
        )
        snapshot = discover(self.root, default_config())
        counts = {f.relative_path: f.line_count for f in snapshot.files}
        self.assertEqual(
            counts,
            {
                "blank_lines.py": 3,
                "crlf.py": 2,
                "empty.py": 0,
                "no_trailing.py": 2,
                "one_newline.py": 1,
                "trailing.py": 2,
            },
        )

    def test_line_counting_spans_read_chunks(self):
        write_tree(self.root, {"big.py": "x = 1\n" * 50_000 + "tail"})
        self.assertEqual(self.file("big.py").line_count, 50_001)

    def test_nul_byte_marks_binary(self):
        write_tree(self.root, {"data.py": b"abc\0def\n"})
        file = self.file("data.py")
        self.assertTrue(file.is_binary)
        self.assertIsNone(file.line_count)
        self.assertEqual(file.classification, FC.BINARY)

    def test_known_binary_extension_is_binary_without_nul(self):
        write_tree(self.root, {"logo.png": "not really an image\n"})
        file = self.file("logo.png")
        self.assertTrue(file.is_binary)
        self.assertIsNone(file.line_count)
        self.assertEqual(file.classification, FC.BINARY)

    def test_non_utf8_text_without_nul_is_text(self):
        write_tree(self.root, {"latin.py": "caf\xe9 = 1\n".encode("latin-1")})
        file = self.file("latin.py")
        self.assertFalse(file.is_binary)
        self.assertEqual(file.line_count, 1)

    def test_generated_header_marker(self):
        write_tree(
            self.root,
            {
                "api.py": "# Code generated by protoc. DO NOT EDIT.\nx = 1\n",
                "late.py": "a\nb\nc\nd\ne\n# @generated\n",
            },
        )
        self.assertEqual(self.file("api.py").classification, FC.GENERATED)
        self.assertEqual(self.file("late.py").classification, FC.SOURCE)


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
class SymlinkTests(DiscoveryTestCase):
    def setUp(self):
        super().setUp()
        self._outside = tempfile.TemporaryDirectory()
        self.outside = Path(self._outside.name)
        write_tree(self.outside, {"secret.py": "x = 1\n", "dir/leak.py": "x = 1\n"})
        write_tree(self.root, {"real/mod.py": "x = 1\n"})

    def tearDown(self):
        self._outside.cleanup()
        super().tearDown()

    def symlink(self, target: Path, name: str, is_dir: bool):
        try:
            os.symlink(target, self.root / name, target_is_directory=is_dir)
        except OSError as exc:  # e.g. Windows without privilege
            self.skipTest(f"cannot create symlink: {exc}")

    def test_symlinks_outside_root_are_not_followed(self):
        self.symlink(self.outside / "dir", "outdir", is_dir=True)
        self.symlink(self.outside / "secret.py", "outfile.py", is_dir=False)
        self.assertEqual(self.paths(), ["real/mod.py"])

    def test_symlinks_inside_root_are_not_followed(self):
        self.symlink(self.root / "real", "alias", is_dir=True)
        self.symlink(self.root / "real" / "mod.py", "alias.py", is_dir=False)
        self.assertEqual(self.paths(), ["real/mod.py"])

    def test_symlink_loop_terminates(self):
        self.symlink(self.root, "loop", is_dir=True)
        self.assertEqual(self.paths(), ["real/mod.py"])

    def test_broken_symlink_is_ignored(self):
        self.symlink(self.root / "missing.py", "broken.py", is_dir=False)
        self.assertEqual(self.paths(), ["real/mod.py"])


class PathIndexTests(DiscoveryTestCase):
    def test_directories_and_unindexed_paths(self):
        write_tree(
            self.root,
            {
                "src/pkg/mod.py": "",
                "docs/a.md": "",
                ".git/HEAD": "",
                "node_modules/x/index.js": "",
                "vendor/lib.py": "",
                "secret.env": "",
            },
        )
        config = parse_config({"repo_check": {"exclude": ["vendor/", "secret.env"]}})
        snapshot = discover(self.root, config)
        self.assertEqual(snapshot.directories, ("docs", "src", "src/pkg"))
        self.assertEqual(
            snapshot.unindexed_paths, (".git", "node_modules", "secret.env", "vendor")
        )

    def test_path_kind(self):
        write_tree(
            self.root, {"src/pkg/mod.py": "", "node_modules/x/index.js": "", "vendor/a.py": ""}
        )
        snapshot = discover(self.root, config_with_exclude("vendor/"))
        cases = {
            "": PathKind.DIRECTORY,
            "src": PathKind.DIRECTORY,
            "src/pkg": PathKind.DIRECTORY,
            "src/pkg/mod.py": PathKind.FILE,
            "node_modules": PathKind.UNINDEXED,
            "node_modules/x/index.js": PathKind.UNINDEXED,
            "node_modules/anything/at/all": PathKind.UNINDEXED,
            "vendor/a.py": PathKind.UNINDEXED,
            "src/missing.py": PathKind.MISSING,
            "src/pkg/mod.py/child": PathKind.MISSING,
            "Src/pkg/mod.py": PathKind.MISSING,
            "node_modules_extra/x": PathKind.MISSING,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertIs(snapshot.path_kind(path), expected)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlinks_are_unindexed(self):
        write_tree(self.root, {"real.py": ""})
        try:
            os.symlink(self.root / "real.py", self.root / "alias.py")
        except OSError as exc:
            self.skipTest(f"cannot create symlink: {exc}")
        snapshot = discover(self.root, default_config())
        self.assertEqual(snapshot.unindexed_paths, ("alias.py",))
        self.assertIs(snapshot.path_kind("alias.py"), PathKind.UNINDEXED)


class ReadTextLinesTests(DiscoveryTestCase):
    def test_decoding_policy_matches_line_count(self):
        contents = {
            "empty.py": b"",
            "plain.py": b"a\nb\n",
            "no_newline.py": b"a\nb",
            "crlf.py": b"a\r\nb\r\n",
            "bom.py": "\ufeffx = 1\n".encode(),
            "invalid.py": b"caf\xe9\n",
            "blank_lines.py": b"a\n\n\n",
        }
        write_tree(self.root, contents)
        snapshot = discover(self.root, default_config())
        lines = {f.relative_path: read_text_lines(f) for f in snapshot.files}
        self.assertEqual(
            lines,
            {
                "blank_lines.py": ("a", "", ""),
                "bom.py": ("x = 1",),
                "crlf.py": ("a", "b"),
                "empty.py": (),
                "invalid.py": ("caf\ufffd",),
                "no_newline.py": ("a", "b"),
                "plain.py": ("a", "b"),
            },
        )
        for file in snapshot.files:
            self.assertEqual(len(lines[file.relative_path]), file.line_count, file.relative_path)

    def test_binary_returns_none(self):
        write_tree(self.root, {"blob.py": b"\0data"})
        self.assertIsNone(read_text_lines(self.file("blob.py")))


class ClassificationTests(unittest.TestCase):
    def assert_class(self, path: str, expected: FC, *, is_binary=False, head=b""):
        actual = classify(PurePosixPath(path), is_binary=is_binary, head=head)
        self.assertEqual(actual, expected, path)

    def test_source(self):
        for path in ("app.py", "src/lib/mod.pyi", "Sources/App/View.swift", "main.c",
                     "src/lib.rs", "web/index.ts", "Main.java", "tool.sh"):
            self.assert_class(path, FC.SOURCE)

    def test_test_by_directory(self):
        for path in ("tests/test_x.py", "tests/helpers.py", "test/util.c",
                     "Tests/AppTests/ViewTests.swift", "src/__tests__/a.js", "spec/a_spec.rb"):
            self.assert_class(path, FC.TEST)

    def test_test_by_filename(self):
        for path in ("pkg/test_parser.py", "pkg/parser_test.go", "Sources/ParserTests.swift",
                     "src/ParserTest.java", "web/button.test.tsx", "web/button.spec.js"):
            self.assert_class(path, FC.TEST)

    def test_near_miss_test_names_are_source(self):
        for path in ("pkg/testing.py", "pkg/contest.py", "src/Test.java", "pkg/latest.py"):
            self.assert_class(path, FC.SOURCE)

    def test_non_source_in_test_directory_keeps_own_class(self):
        self.assert_class("tests/fixtures/data.json", FC.CONFIGURATION)
        self.assert_class("tests/README.md", FC.DOCUMENTATION)
        self.assert_class("tests/fixtures/sample.txt", FC.UNKNOWN)

    def test_documentation(self):
        for path in ("README.md", "docs/guide.rst", "CHANGELOG", "LICENSE", "LICENSE.txt",
                     "docs/intro.markdown"):
            self.assert_class(path, FC.DOCUMENTATION)

    def test_configuration(self):
        for path in ("pyproject.toml", "repo-check.toml", ".github/workflows/ci.yml",
                     "setup.cfg", "tsconfig.json", ".gitignore", "requirements.txt",
                     "Info.plist"):
            self.assert_class(path, FC.CONFIGURATION)

    def test_generated_by_name(self):
        for path in ("package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock",
                     "static/app.min.js", "proto/api_pb2.py", "Package.resolved"):
            self.assert_class(path, FC.GENERATED)

    def test_generated_by_header(self):
        self.assert_class("api.py", FC.GENERATED, head=b"# @generated by tool\nx = 1\n")

    def test_build_artifacts(self):
        for path in ("build/lib/pkg/mod.py", "dist/app.js", "Build/out.c",
                     "src/pkg.egg-info/PKG-INFO", "pkg/mod.pyc", "Main.class", "lib.so"):
            self.assert_class(path, FC.BUILD_ARTIFACT)

    def test_top_level_build_and_dist_directories(self):
        for path in ("build/file.py", "dist/file.py", "build/sub/file.md", "DIST/pkg.py"):
            self.assert_class(path, FC.BUILD_ARTIFACT)

    def test_nested_build_and_dist_directories_keep_natural_class(self):
        # Only the first directory component counts for the generic names.
        self.assert_class("docs/manuals/build/index.md", FC.DOCUMENTATION)
        self.assert_class("src/mypkg/dist/core.py", FC.SOURCE)
        self.assert_class("packages/foo/build/source.py", FC.SOURCE)
        self.assert_class("pkg/Build/out.c", FC.SOURCE)
        self.assert_class("tests/dist/test_core.py", FC.TEST)
        self.assert_class("src/build/config.json", FC.CONFIGURATION)
        # A file named build or dist is not a directory component.
        self.assert_class("build", FC.UNKNOWN)
        self.assert_class("src/dist.py", FC.SOURCE)

    def test_distinctive_build_outputs_match_at_any_depth(self):
        for path in (".build/file", "Packages/Foo/.build/output.o", "Packages/Foo/.build/x",
                     "a/b/something.egg-info/file", "deep/nested/dir/native.so",
                     "x/y/Main.class"):
            self.assert_class(path, FC.BUILD_ARTIFACT)

    def test_build_artifact_takes_precedence_over_binary(self):
        self.assert_class("mod.pyc", FC.BUILD_ARTIFACT, is_binary=True)
        self.assert_class("dist/logo.png", FC.BUILD_ARTIFACT, is_binary=True)

    def test_binary(self):
        self.assert_class("assets/logo.png", FC.BINARY, is_binary=True)
        self.assert_class("data.py", FC.BINARY, is_binary=True)

    def test_unknown(self):
        for path in ("Makefile", "data.csv", "notes.txt", "Dockerfile", "schema.sql"):
            self.assert_class(path, FC.UNKNOWN)

    def test_similar_directory_names_are_not_build_dirs(self):
        self.assert_class("builder/mod.py", FC.SOURCE)
        self.assert_class("distribution/mod.py", FC.SOURCE)


if __name__ == "__main__":
    unittest.main()
