import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_check.config import default_config, parse_config
from repo_check.discovery import discover, is_path_excluded
from repo_check.git import read_git_state
from repo_check.git import client as git_client
from repo_check.git.client import parse_ls_files
from repo_check.model import GitState

from support import git, make_git_repo, requires_git, write_tree


class GitTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()


@requires_git
class ReadGitStateTests(GitTestCase):
    def test_detects_repository_and_tracked_paths(self):
        root = make_git_repo(
            self.tmp / "repo",
            tracked={"src/app.py": "x = 1\n", "README.md": "# r\n"},
            untracked={"notes.txt": "draft\n", "build/out.o": b"\0"},
        )
        state = read_git_state(root)
        self.assertEqual(
            state,
            GitState(
                available=True,
                repository_root=root,
                tracked_paths=("README.md", "src/app.py"),
            ),
        )

    def test_non_git_directory_is_unavailable(self):
        write_tree(self.tmp, {"a.py": ""})
        self.assertEqual(read_git_state(self.tmp), GitState.unavailable())
        self.assertEqual(
            (GitState.unavailable().repository_root, GitState.unavailable().tracked_paths),
            (None, ()),
        )

    def test_repository_with_no_commits_or_files(self):
        root = make_git_repo(self.tmp / "empty", tracked={})
        self.assertEqual(read_git_state(root), GitState(True, root, ()))

    def test_staged_but_uncommitted_file_is_tracked(self):
        root = make_git_repo(self.tmp / "repo", tracked={"a.py": ""})
        write_tree(root, {"b.py": ""})
        git(root, "add", "b.py")
        self.assertEqual(read_git_state(root).tracked_paths, ("a.py", "b.py"))

    def test_deterministic_ordering(self):
        # "C.py" sorts before "b.py" in plain code-point order (not in a
        # case-insensitive one). No two names differ only by case, so this also
        # runs on case-insensitive file systems.
        names = ["b.py", "a/z.py", "a.txt", "C.py", "a/b/c.py", "_x.py", "é.py"]
        root = make_git_repo(self.tmp / "repo", tracked={n: "" for n in reversed(names)})
        expected = ("C.py", "_x.py", "a.txt", "a/b/c.py", "a/z.py", "b.py", "é.py")
        self.assertEqual(expected, tuple(sorted(names)))
        self.assertEqual(read_git_state(root).tracked_paths, expected)
        self.assertEqual(read_git_state(root).tracked_paths, expected)

    def test_filenames_with_spaces_and_special_characters(self):
        names = ["dir with space/file name.o", "lead space.py"]
        if os.name != "nt":  # not valid Windows filenames
            names += ["tab\there.py", 'quote".py', "new\nline.py"]
        root = make_git_repo(self.tmp / "repo", tracked={n: "" for n in names})
        self.assertEqual(read_git_state(root).tracked_paths, tuple(sorted(names)))

    def test_subdirectory_analysis_is_scoped(self):
        repo = make_git_repo(
            self.tmp / "repo",
            tracked={"top.o": b"\0", "pkg/a.py": "", "pkg/build/x.o": b"\0", "other/b.o": b"\0",
                     "pkgs/c.py": ""},
        )
        state = read_git_state(repo / "pkg")
        self.assertEqual(state, GitState(True, repo, ("a.py", "build/x.o")))

    def test_nested_repository_root_reported_from_deep_subdirectory(self):
        repo = make_git_repo(self.tmp / "repo", tracked={"a/b/c/d.py": ""})
        self.assertEqual(read_git_state(repo / "a" / "b"), GitState(True, repo, ("c/d.py",)))

    def test_inside_git_directory_is_unavailable(self):
        repo = make_git_repo(self.tmp / "repo", tracked={"a.py": ""})
        self.assertEqual(read_git_state(repo / ".git"), GitState.unavailable())

    @unittest.skipIf(os.name == "nt", "uses a POSIX shell script as the hook")
    def test_repository_fsmonitor_hook_is_not_executed(self):
        repo = make_git_repo(self.tmp / "repo", tracked={"a.py": ""})
        marker = self.tmp / "hook-ran"
        hook = self.tmp / "hook.sh"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nprintf '/\\0'\n", encoding="utf-8")
        hook.chmod(0o755)
        git(repo, "config", "core.fsmonitor", str(hook))
        # Sanity check: plain Git does run the configured hook.
        git(repo, "status", "--porcelain")
        if not marker.exists():
            self.skipTest("this git version does not invoke core.fsmonitor hooks")
        marker.unlink()
        self.assertEqual(read_git_state(repo).tracked_paths, ("a.py",))
        self.assertFalse(marker.exists())

    def test_ambient_git_dir_is_ignored(self):
        repo = make_git_repo(self.tmp / "repo", tracked={"a.py": ""})
        other = make_git_repo(self.tmp / "other", tracked={"zzz.py": ""})
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git"),
                                          "GIT_WORK_TREE": str(other)}):
            self.assertEqual(read_git_state(repo).tracked_paths, ("a.py",))


class GitClientBoundaryTests(GitTestCase):
    def test_missing_git_executable_is_unavailable(self):
        missing = str(self.tmp / "no-such-git")
        self.assertEqual(read_git_state(self.tmp, git_executable=missing), GitState.unavailable())

    def test_git_on_empty_path_is_unavailable(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp)}):
            self.assertEqual(read_git_state(self.tmp), GitState.unavailable())

    def test_subprocess_is_run_without_shell_and_with_argument_list(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            stdout = b"/repo\n" if "rev-parse" in args else b"a.py\0dir/b c.o\0"
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=b"")

        with mock.patch.object(git_client.subprocess, "run", side_effect=fake_run):
            state = read_git_state(Path("/repo/sub"))

        self.assertEqual(state, GitState(True, Path("/repo"), ("a.py", "dir/b c.o")))
        self.assertEqual(
            [args for args, _ in calls],
            [
                ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", "/repo/sub",
                 "rev-parse", "--show-toplevel"],
                ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", "/repo/sub",
                 "ls-files", "-z"],
            ],
        )
        for args, kwargs in calls:
            self.assertIsInstance(args, list)
            self.assertIs(kwargs.get("shell"), False)
            self.assertIn("timeout", kwargs)

    def test_git_failure_is_unavailable(self):
        def failing_ls_files(args, **kwargs):
            code = 0 if "rev-parse" in args else 128
            return subprocess.CompletedProcess(args, code, stdout=b"/repo\n", stderr=b"fatal")

        with mock.patch.object(git_client.subprocess, "run", side_effect=failing_ls_files):
            self.assertEqual(read_git_state(Path("/repo")), GitState.unavailable())

    def test_timeout_is_unavailable(self):
        timeout = subprocess.TimeoutExpired(["git"], 60)
        with mock.patch.object(git_client.subprocess, "run", side_effect=timeout):
            self.assertEqual(read_git_state(Path("/repo")), GitState.unavailable())

    def test_parse_ls_files_drops_paths_outside_root_and_normalizes(self):
        output = b"b.py\0a.py\0../outside.py\0/abs.py\0./dot.py\0a//b.py\0a.py\0\0"
        self.assertEqual(parse_ls_files(output), ("a.py", "b.py"))


@requires_git
class SnapshotGitStateTests(GitTestCase):
    def test_snapshot_applies_exclusions_to_tracked_paths(self):
        root = make_git_repo(
            self.tmp / "repo",
            tracked={"vendor/lib/x.o": b"\0", "src/a.py": "", "gen/b.o": b"\0", "keep.o": b"\0"},
        )
        config = parse_config({"repo_check": {"exclude": ["vendor/", "gen/*.o"]}})
        snapshot = discover(root, config, read_git_state(root))
        self.assertEqual(snapshot.git.tracked_paths, ("keep.o", "src/a.py"))
        self.assertTrue(snapshot.git.available)
        self.assertEqual(snapshot.git.repository_root, root)

    def test_tracked_paths_include_pruned_directories(self):
        root = make_git_repo(
            self.tmp / "repo",
            tracked={"__pycache__/m.cpython-311.pyc": b"\0", "node_modules/x/index.js": "",
                     "src/a.py": ""},
        )
        snapshot = discover(root, default_config(), read_git_state(root))
        self.assertEqual([f.relative_path for f in snapshot.files], ["src/a.py"])
        self.assertEqual(
            snapshot.git.tracked_paths,
            ("__pycache__/m.cpython-311.pyc", "node_modules/x/index.js", "src/a.py"),
        )

    def test_discover_without_git_state_is_unavailable(self):
        write_tree(self.tmp, {"a.py": ""})
        self.assertEqual(discover(self.tmp, default_config()).git, GitState.unavailable())


class PathExclusionTests(unittest.TestCase):
    def test_matches_directory_walk_semantics(self):
        patterns = ("vendor/", "*.min.js", "docs", "*/cache/")
        cases = {
            "vendor/a.o": True,
            "src/vendor/a.o": False,
            "vendor": False,  # a file named vendor: "vendor/" is directory-only
            "web/app.min.js": True,
            "docs/x.md": True,
            "docs": True,
            "a/cache/b.o": True,
            "cache/b.o": False,
            "src/app.py": False,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(is_path_excluded(path, patterns), expected)


if __name__ == "__main__":
    unittest.main()
