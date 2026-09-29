import tempfile
import unittest
from pathlib import Path, PurePosixPath

from repo_check.config import parse_config
from repo_check.discovery import (
    BUILD_ARTIFACT_EXTENSIONS,
    classify,
    discover,
    is_build_artifact_path,
)
from repo_check.engine import analyze, run_rules
from repo_check.model import FileClassification, Finding, GitState, Severity
from repo_check.rules import REPOSITORY_RULES
from repo_check.rules.common.tracked_build_artifact import TrackedBuildArtifact

from support import git, make_git_repo, numbered_lines, requires_git, write_tree


def expected(path: str) -> Finding:
    return Finding(
        rule_id="TRACKED_BUILD_ARTIFACT",
        severity=Severity.WARNING,
        message="Build artifact is tracked by Git.",
        path=path,
        location=None,
        evidence={"classification": "build_artifact", "tracked": True},
    )


class MetadataTests(unittest.TestCase):
    def test_metadata(self):
        metadata = TrackedBuildArtifact.metadata
        self.assertEqual(metadata.id, "TRACKED_BUILD_ARTIFACT")
        self.assertEqual(metadata.default_severity, Severity.WARNING)
        self.assertTrue(metadata.title and metadata.category)
        self.assertTrue(metadata.description and metadata.remediation)

    def test_registered_as_repository_rule(self):
        self.assertEqual(
            [r.metadata.id for r in REPOSITORY_RULES],
            ["BROKEN_LOCAL_DOC_LINK", "TRACKED_BUILD_ARTIFACT"],
        )


class SnapshotOnlyTests(unittest.TestCase):
    """The rule reads only snapshot.git; it needs no real repository."""

    def findings(self, git_state: GitState, config=None):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = discover(Path(tmp), config or parse_config({}), git_state)
            return list(run_rules(snapshot, (), REPOSITORY_RULES))

    def test_unavailable_git_state_produces_nothing(self):
        self.assertEqual(self.findings(GitState.unavailable()), [])

    def test_reports_from_git_state_without_files_on_disk(self):
        state = GitState(True, Path("/r"), ("build/a.o", "src/a.py"))
        self.assertEqual(self.findings(state), [expected("build/a.o")])


@requires_git
class TrackedBuildArtifactTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def repo(self, tracked, untracked=None, config_text=None):
        root = make_git_repo(self.tmp / "repo", tracked, untracked)
        if config_text is not None:
            (root / "repo-check.toml").write_text(config_text, encoding="utf-8")
        return root

    def findings(self, root):
        return [f for f in analyze(root).findings if f.rule_id == "TRACKED_BUILD_ARTIFACT"]

    # Positive cases

    def test_tracked_file_under_build(self):
        root = self.repo({"build/output.o": b"\0", "build/lib/pkg/mod.py": "x = 1\n"})
        self.assertEqual(
            self.findings(root), [expected("build/lib/pkg/mod.py"), expected("build/output.o")]
        )

    def test_tracked_file_under_dist(self):
        # Only a top-level dist/ is a build-output directory.
        root = self.repo({"dist/app.js": "x;\n", "web/dist/bundle.css": "a{}\n"})
        self.assertEqual(self.findings(root), [expected("dist/app.js")])

    def test_tracked_egg_info_contents(self):
        root = self.repo({"src/pkg.egg-info/PKG-INFO": "Name: pkg\n"})
        self.assertEqual(self.findings(root), [expected("src/pkg.egg-info/PKG-INFO")])

    def test_tracked_compiled_extensions(self):
        root = self.repo(
            {"src/a.o": b"\0", "Main.class": b"\0", "lib/native.so": b"\0", "m.pyc": b"\0"}
        )
        self.assertEqual(
            self.findings(root),
            [expected("Main.class"), expected("lib/native.so"), expected("m.pyc"),
             expected("src/a.o")],
        )

    def test_tracked_artifact_in_pruned_directory(self):
        root = self.repo({"pkg/__pycache__/mod.cpython-311.pyc": b"\0", "pkg/mod.py": ""})
        result = analyze(root)
        # Discovery never visits the pruned directory ...
        self.assertEqual([f.relative_path for f in result.snapshot.files], ["pkg/mod.py"])
        # ... but Git state still reports the tracked artifact.
        self.assertEqual(list(result.findings), [expected("pkg/__pycache__/mod.cpython-311.pyc")])

    def test_tracked_swiftpm_build_directory_not_visited_by_discovery(self):
        # Neither path has a build-artifact extension: ".build/" alone is the reason.
        root = self.repo(
            {
                ".build/debug/App": "#!binary placeholder\n",
                ".build/checkouts/example/source.swift": "let x = 1\n",
                "Sources/App/main.swift": "print(1)\n",
            }
        )
        for path in (".build/debug/App", ".build/checkouts/example/source.swift"):
            self.assertNotIn(PurePosixPath(path).suffix.lower(), BUILD_ARTIFACT_EXTENSIONS)
        result = analyze(root)
        self.assertEqual(
            [f.relative_path for f in result.snapshot.files], ["Sources/App/main.swift"]
        )
        self.assertEqual(
            list(result.findings),
            [expected(".build/checkouts/example/source.swift"), expected(".build/debug/App")],
        )

    def test_tracked_artifact_matched_by_gitignore_is_still_reported(self):
        root = self.repo({".gitignore": "build/\n*.o\n", "build/output.o": b"\0"})
        # The ignore rules do match the file ...
        self.assertEqual(
            git(root, "check-ignore", "--no-index", "build/output.o").strip(), "build/output.o"
        )
        # ... but it is tracked, so it is reported.
        self.assertEqual(self.findings(root), [expected("build/output.o")])

    def test_uppercase_build_directory(self):
        root = self.repo({"Build/out.txt": "x\n"})
        self.assertEqual(self.findings(root), [expected("Build/out.txt")])

    def test_path_with_spaces(self):
        root = self.repo({"dist/my build/app bundle.js": "x;\n"})
        self.assertEqual(self.findings(root), [expected("dist/my build/app bundle.js")])

    # Negative / near-miss cases

    def test_untracked_identical_artifact(self):
        root = self.repo({"src/app.py": ""}, untracked={"build/output.o": b"\0"})
        self.assertEqual(self.findings(root), [])

    def test_ignored_untracked_artifact(self):
        root = self.repo({".gitignore": "build/\n"}, untracked={"build/output.o": b"\0"})
        self.assertEqual(self.findings(root), [])

    def test_tracked_source_documentation_and_config(self):
        root = self.repo(
            {"src/app.py": "", "README.md": "", "pyproject.toml": "", "docs/build.md": "",
             ".github/workflows/build.yml": "", "package-lock.json": "{}", "app.min.js": ""}
        )
        self.assertEqual(self.findings(root), [])

    def test_similar_but_not_artifact_names(self):
        root = self.repo(
            {"builder/mod.py": "", "distribution/mod.py": "", "build.py": "", "dist.txt": "",
             "src/build_utils.py": "", "rebuild/x.py": "", "dists/x.py": "", "src/o.py": "",
             "notes.so.txt": "", "egg-info/x.py": "", "gradle/wrapper/gradle-wrapper.jar": b"\0",
             "wheels/pkg-1.0-py3-none-any.whl": b"\0", "my.build/x.py": "", ".builds/x.py": "",
             "src/.build.py": ""}
        )
        self.assertEqual(self.findings(root), [])

    def test_nested_build_and_dist_directories_are_not_reported(self):
        root = self.repo(
            {"docs/manuals/build/index.md": "# Build\n", "src/mypkg/dist/core.py": "x = 1\n",
             "packages/foo/build/source.py": "", "my build/dist/app bundle.js": "x;\n"}
        )
        self.assertEqual(self.findings(root), [])

    def test_nested_distinctive_build_outputs_are_still_reported(self):
        root = self.repo(
            {"Packages/Foo/.build/debug/App": "bin\n", "src/mypkg/dist/core.o": b"\0",
             "lib/pkg.egg-info/PKG-INFO": "Name: pkg\n"}
        )
        self.assertEqual(
            self.findings(root),
            [expected("Packages/Foo/.build/debug/App"), expected("lib/pkg.egg-info/PKG-INFO"),
             expected("src/mypkg/dist/core.o")],
        )

    def test_nested_files_regain_natural_analysis(self):
        root = self.repo(
            {
                "docs/manuals/build/index.md": "# Docker Build\nSee [missing](gone.md).\n",
                "src/mypkg/dist/core.py": "def f(x=[]):\n    return x  # TODO fix\n",
                "repo-check.toml": "[repo_check.rules.BROKEN_LOCAL_DOC_LINK]\nenabled = true\n",
            }
        )
        result = analyze(root)
        self.assertEqual(
            {f.relative_path: f.classification for f in result.snapshot.files
             if f.relative_path != "repo-check.toml"},
            {"docs/manuals/build/index.md": FileClassification.DOCUMENTATION,
             "src/mypkg/dist/core.py": FileClassification.SOURCE},
        )
        self.assertEqual(
            [(f.rule_id, f.path, f.location.start_line) for f in result.findings],
            [("BROKEN_LOCAL_DOC_LINK", "docs/manuals/build/index.md", 2),
             ("PYTHON_MUTABLE_DEFAULT", "src/mypkg/dist/core.py", 1),
             ("TODO_COMMENT", "src/mypkg/dist/core.py", 2)],
        )

    def test_excluded_tracked_artifact(self):
        root = self.repo(
            {"build/output.o": b"\0", "dist/keep.js": "", "vendor/lib.so": b"\0"},
            config_text='[repo_check]\nexclude = ["build/", "vendor"]\n',
        )
        self.assertEqual(self.findings(root), [expected("dist/keep.js")])

    def test_excluded_artifact_inside_pruned_directory(self):
        root = self.repo(
            {"pkg/__pycache__/m.pyc": b"\0"},
            config_text='[repo_check]\nexclude = ["*/__pycache__/"]\n',
        )
        self.assertEqual(self.findings(root), [])

    def test_rule_disabled(self):
        root = self.repo(
            {"build/output.o": b"\0"},
            config_text="[repo_check.rules.TRACKED_BUILD_ARTIFACT]\nenabled = false\n",
        )
        self.assertEqual(self.findings(root), [])

    def test_non_git_directory(self):
        write_tree(self.tmp, {"build/output.o": b"\0", "dist/app.js": ""})
        self.assertEqual(self.findings(self.tmp), [])

    def test_subdirectory_analysis_reports_paths_relative_to_analysis_root(self):
        root = self.repo({"build/top.o": b"\0", "pkg/build/inner.o": b"\0", "other/x.o": b"\0"})
        self.assertEqual(self.findings(root / "pkg"), [expected("build/inner.o")])

    def test_nested_build_directory_is_top_level_when_analyzed_directly(self):
        root = self.repo({"packages/foo/build/source.py": "x = 1\n"})
        self.assertEqual(self.findings(root), [])
        self.assertEqual(self.findings(root / "packages" / "foo"), [expected("build/source.py")])

    def test_classification_is_relative_to_analysis_root(self):
        # As for discovery, directory context above the analysis root is not
        # used: analyzing dist/ itself sees "app.js", not "dist/app.js".
        root = self.repo({"dist/app.js": "x;\n", "dist/native.o": b"\0"})
        self.assertEqual(self.findings(root / "dist"), [expected("native.o")])


@requires_git
class ScopeInteractionTests(unittest.TestCase):
    def test_file_and_repository_findings_share_sorting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_git_repo(
                Path(tmp) / "repo",
                tracked={
                    "a.py": numbered_lines(1001),
                    "build/gen.py": numbered_lines(1001),
                    "dist/x.o": b"\0",
                    "z.py": numbered_lines(1001),
                },
            )
            findings = list(analyze(root).findings)
        self.assertEqual(
            [(f.path, f.rule_id) for f in findings],
            [
                ("a.py", "FILE_TOO_LARGE"),
                ("build/gen.py", "TRACKED_BUILD_ARTIFACT"),
                ("dist/x.o", "TRACKED_BUILD_ARTIFACT"),
                ("z.py", "FILE_TOO_LARGE"),
            ],
        )


class ClassificationReuseTests(unittest.TestCase):
    def test_path_helper_agrees_with_classify(self):
        paths = [
            "build/a.py", "dist/x.js", "src/pkg.egg-info/PKG-INFO", "a.o", "B.CLASS", "m.pyc",
            "Build/x", ".build/debug/App", "pkg/.build/x.swift", ".BUILD/x", ".builds/x",
            "my.build/x", "src/app.py", "builder/a.py", "README.md", "package-lock.json",
            "docs/manuals/build/index.md", "src/mypkg/dist/core.py", "pkg/Build/x",
            "pkg/dist/lib.so", "a/b/pkg.egg-info/PKG-INFO",
            "lib.jar", "pkg.whl", "static/app.min.js", "tests/test_x.py", "logo.png",
        ]
        for path in paths:
            for is_binary in (False, True):
                with self.subTest(path=path, is_binary=is_binary):
                    self.assertEqual(
                        is_build_artifact_path(path),
                        classify(PurePosixPath(path), is_binary=is_binary)
                        is FileClassification.BUILD_ARTIFACT,
                    )

    def test_swiftpm_build_directory_is_build_artifact(self):
        self.assertTrue(is_build_artifact_path(".build/debug/App"))
        self.assertTrue(is_build_artifact_path("pkg/.build/checkouts/dep/Package.swift"))
        self.assertFalse(is_build_artifact_path(".build"))  # the directory name alone
        self.assertFalse(is_build_artifact_path("my.build/x.swift"))
        self.assertFalse(is_build_artifact_path(".builds/x.swift"))

    def test_jar_and_wheel_are_binary_not_build_artifacts(self):
        for path in ("gradle/wrapper/gradle-wrapper.jar", "wheels/pkg-1.0-py3-none-any.whl"):
            with self.subTest(path=path):
                self.assertEqual(
                    classify(PurePosixPath(path), is_binary=True), FileClassification.BINARY
                )


if __name__ == "__main__":
    unittest.main()
