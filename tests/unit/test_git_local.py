import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_check.config import parse_config
from repo_check.discovery import discover
from repo_check.errors import LocalChangeError
from repo_check.git import client as git_client
from repo_check.git.client import (
    parse_untracked_paths,
    read_git_staged_changes,
    read_git_working_tree_changes,
)
from repo_check.model import ChangeSource, ChangeStatus as S, GitChange as C

from support import git, make_git_repo, requires_git, write_tree


@requires_git
class LocalChangeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_git_repo(Path(self._tmp.name) / "repo", {"base.py": "old\n"})

    def tearDown(self):
        self._tmp.cleanup()

    def staged(self, root=None):
        result = read_git_staged_changes(root or self.repo)
        self.assertIs(result.source, ChangeSource.STAGED)
        return result.changes

    def working(self, root=None):
        result = read_git_working_tree_changes(root or self.repo)
        self.assertIs(result.source, ChangeSource.WORKING_TREE)
        return result.changes

    def test_clean_repository(self):
        self.assertEqual(self.staged(), ())
        self.assertEqual(self.working(), ())

    def test_staged_add_modify_delete_excludes_unstaged_and_untracked(self):
        write_tree(self.repo, {"base.py": "staged\n", "added.py": "new\n"})
        git(self.repo, "add", "base.py", "added.py")
        write_tree(self.repo, {"base.py": "unstaged\n", "loose.py": "untracked\n"})
        self.assertEqual(self.staged(), (C(S.ADDED, "added.py"), C(S.MODIFIED, "base.py")))
        self.assertEqual(
            self.working(),
            (C(S.ADDED, "added.py"), C(S.MODIFIED, "base.py"), C(S.ADDED, "loose.py")),
        )
        git(self.repo, "rm", "-f", "base.py")
        self.assertEqual(self.staged(), (C(S.ADDED, "added.py"), C(S.DELETED, "base.py")))
        self.assertEqual(
            self.working(),
            (C(S.ADDED, "added.py"), C(S.DELETED, "base.py"), C(S.ADDED, "loose.py")),
        )

    def test_working_tree_final_state_overlapping_layers(self):
        write_tree(self.repo, {"base.py": "staged\n", "new.py": "staged\n"})
        git(self.repo, "add", "base.py", "new.py")
        write_tree(self.repo, {"base.py": "final\n", "new.py": "final\n"})
        self.assertEqual(self.working(), (C(S.MODIFIED, "base.py"), C(S.ADDED, "new.py")))
        (self.repo / "base.py").unlink()
        self.assertEqual(self.working(), (C(S.DELETED, "base.py"), C(S.ADDED, "new.py")))
        self.assertEqual(self.staged(), (C(S.MODIFIED, "base.py"), C(S.ADDED, "new.py")))

    def test_unstaged_only_and_staged_only(self):
        write_tree(self.repo, {"base.py": "unstaged\n"})
        self.assertEqual(self.staged(), ())
        self.assertEqual(self.working(), (C(S.MODIFIED, "base.py"),))
        git(self.repo, "add", "base.py")
        self.assertEqual(self.staged(), (C(S.MODIFIED, "base.py"),))
        self.assertEqual(self.working(), (C(S.MODIFIED, "base.py"),))

    def test_untracked_respects_ignore_rules(self):
        write_tree(self.repo, {".gitignore": "ignored/\n*.tmp\n", "ignored/build.py": "x",
                               "scratch.tmp": "x", "keep/new.py": "x"})
        self.assertEqual(self.staged(), ())
        self.assertEqual(self.working(), (C(S.ADDED, ".gitignore"), C(S.ADDED, "keep/new.py")))

    def test_unstaged_delete_and_similar_untracked_file_are_separate(self):
        original = "".join(f"line {i}\n" for i in range(30))
        write_tree(self.repo, {"old.py": original})
        git(self.repo, "add", "old.py")
        git(self.repo, "commit", "-qm", "old path")
        (self.repo / "old.py").unlink()
        write_tree(self.repo, {"new.py": original})
        self.assertEqual(self.staged(), ())
        self.assertEqual(self.working(), (C(S.ADDED, "new.py"), C(S.DELETED, "old.py")))

    def test_index_removed_path_recreated_in_worktree_uses_final_content(self):
        git(self.repo, "rm", "--cached", "-f", "base.py")
        self.assertEqual(self.staged(), (C(S.DELETED, "base.py"),))
        self.assertEqual(self.working(), ())  # identical to HEAD
        write_tree(self.repo, {"base.py": "recreated\n"})
        self.assertEqual(self.working(), (C(S.MODIFIED, "base.py"),))

    @unittest.skipIf(os.name == "nt", "shell script fixture")
    def test_recreated_untracked_file_does_not_run_clean_filter(self):
        marker = Path(self._tmp.name) / "filter-ran"
        script = Path(self._tmp.name) / "clean-filter.sh"
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
        script.chmod(0o755)
        write_tree(self.repo, {".gitattributes": "base.py filter=trap\n"})
        git(self.repo, "add", ".gitattributes")
        git(self.repo, "commit", "-qm", "add attributes")
        git(self.repo, "rm", "--cached", "base.py")
        git(self.repo, "config", "filter.trap.clean", str(script))
        self.assertEqual(self.working(), ())
        self.assertFalse(marker.exists())
        write_tree(self.repo, {"base.py": "raw changed\n"})
        self.assertEqual(self.working(), (C(S.MODIFIED, "base.py"),))
        self.assertFalse(marker.exists())

    def test_rename_then_further_edit_preserves_identity_when_git_detects_it(self):
        original = "".join(f"line {i}: stable rename content\n" for i in range(30))
        write_tree(self.repo, {"long.py": original})
        git(self.repo, "add", "long.py")
        git(self.repo, "commit", "-qm", "long base")
        git(self.repo, "mv", "long.py", "moved.py")
        self.assertEqual(self.staged(), (C(S.RENAMED, "moved.py", "long.py"),))
        write_tree(self.repo, {"moved.py": original + "one more line\n"})
        self.assertEqual(self.working(), (C(S.RENAMED, "moved.py", "long.py"),))

    def test_copy_detection_matches_comparison_policy(self):
        original = "".join(f"line {i}: stable copy content\n" for i in range(30))
        write_tree(self.repo, {"source.py": original})
        git(self.repo, "add", "source.py")
        git(self.repo, "commit", "-qm", "copy base")
        write_tree(self.repo, {"source.py": original + "extra\n", "copy.py": original})
        git(self.repo, "add", "source.py", "copy.py")
        expected = (C(S.COPIED, "copy.py", "source.py"), C(S.MODIFIED, "source.py"))
        self.assertEqual(self.staged(), expected)
        self.assertEqual(self.working(), expected)

    @unittest.skipIf(os.name == "nt", "symlinks")
    def test_staged_and_working_type_change(self):
        (self.repo / "base.py").unlink()
        os.symlink("target.py", self.repo / "base.py")
        git(self.repo, "add", "base.py")
        self.assertEqual(self.staged(), (C(S.TYPE_CHANGED, "base.py"),))
        self.assertEqual(self.working(), (C(S.TYPE_CHANGED, "base.py"),))

    def test_unusual_names_and_deterministic_order(self):
        names = ["b.py", "a/z.py", "C.py", "tab\there.py", "new\nline.py", "é.py"]
        write_tree(self.repo, {name: "x\n" for name in names})
        expected = tuple(C(S.ADDED, name) for name in sorted(names))
        self.assertEqual(self.working(), expected)
        git(self.repo, "add", "--", *names)
        self.assertEqual(self.staged(), expected)
        self.assertEqual(self.working(), expected)

    def test_subdirectory_and_cross_boundary_renames(self):
        write_tree(self.repo, {"pkg/in.py": "same\n", "pkg/keep.py": "keep\n",
                               "outside.py": "outside\n", "pkgs/other.py": "other\n"})
        git(self.repo, "add", "pkg/in.py", "pkg/keep.py", "outside.py", "pkgs/other.py")
        git(self.repo, "commit", "-qm", "paths")
        git(self.repo, "mv", "outside.py", "pkg/enter.py")
        git(self.repo, "mv", "pkg/in.py", "depart.py")
        # Sorting is by the resulting path, so enter.py precedes in.py.
        expected = (C(S.ADDED, "enter.py"), C(S.DELETED, "in.py"))
        self.assertEqual(self.staged(self.repo / "pkg"), expected)
        self.assertEqual(self.working(self.repo / "pkg"), expected)
        self.assertEqual(self.staged(self.repo / "pkgs"), ())
        self.assertEqual(self.working(self.repo / "pkgs"), ())

    def test_copy_across_boundary_does_not_change_source_directory(self):
        write_tree(self.repo, {"pkg/source.py": "original\n", "outside/keep.py": "x\n"})
        git(self.repo, "add", "pkg/source.py", "outside/keep.py")
        git(self.repo, "commit", "-qm", "copy base")
        write_tree(self.repo, {"outside/copy.py": "original\n"})
        git(self.repo, "add", "outside/copy.py")
        self.assertEqual(self.staged(self.repo / "pkg"), ())
        self.assertEqual(self.working(self.repo / "pkg"), ())
        self.assertEqual(self.staged(self.repo / "outside"), (C(S.ADDED, "copy.py"),))

    def test_working_tree_copies_across_subdirectory_boundary(self):
        inside = "".join(f"inside line {i}\n" for i in range(30))
        outside = "".join(f"outside line {i}\n" for i in range(30))
        write_tree(self.repo, {"pkg/source.py": inside, "outside/source.py": outside})
        git(self.repo, "add", "pkg/source.py", "outside/source.py")
        git(self.repo, "commit", "-qm", "copy sources")
        write_tree(self.repo, {
            "pkg/source.py": inside + "changed\n",
            "outside/source.py": outside + "changed\n",
            "pkg/from_outside.py": outside,
            "outside/from_pkg.py": inside,
        })
        git(self.repo, "add", "pkg/source.py", "outside/source.py",
            "pkg/from_outside.py", "outside/from_pkg.py")
        self.assertEqual(
            self.working(self.repo),
            (C(S.COPIED, "outside/from_pkg.py", "pkg/source.py"),
             C(S.MODIFIED, "outside/source.py"),
             C(S.COPIED, "pkg/from_outside.py", "outside/source.py"),
             C(S.MODIFIED, "pkg/source.py")),
        )
        self.assertEqual(
            self.working(self.repo / "pkg"),
            (C(S.ADDED, "from_outside.py"), C(S.MODIFIED, "source.py")),
        )

    def test_untracked_paths_are_scoped_to_analysis_subdirectory(self):
        write_tree(self.repo, {"pkg/anchor.py": "x\n", ".gitignore": "*.tmp\n"})
        git(self.repo, "add", "pkg/anchor.py", ".gitignore")
        git(self.repo, "commit", "-qm", "subdirectory base")
        write_tree(self.repo, {"pkg/new.py": "x\n", "outside.py": "x\n",
                               "pkg/ignored.tmp": "x\n", "outside.tmp": "x\n"})
        self.assertEqual(self.working(self.repo / "pkg"), (C(S.ADDED, "new.py"),))

    def test_repository_configuration_cannot_disable_rename_detection(self):
        write_tree(self.repo, {"long.py": "".join(f"line {i}\n" for i in range(30))})
        git(self.repo, "add", "long.py")
        git(self.repo, "commit", "-qm", "long base")
        git(self.repo, "config", "diff.renames", "false")
        git(self.repo, "mv", "long.py", "renamed.py")
        expected = (C(S.RENAMED, "renamed.py", "long.py"),)
        self.assertEqual(self.staged(), expected)
        self.assertEqual(self.working(), expected)

    @unittest.skipIf(os.name == "nt", "shell script fixture")
    def test_external_diff_configuration_is_not_executed(self):
        marker = self.repo / "external-ran"
        script = self.repo / "external-diff.sh"
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        script.chmod(0o755)
        git(self.repo, "config", "diff.external", str(script))
        write_tree(self.repo, {"base.py": "changed\n"})
        git(self.repo, "add", "base.py")
        self.assertEqual(self.staged(), (C(S.MODIFIED, "base.py"),))
        self.assertEqual(self.working(), (C(S.MODIFIED, "base.py"), C(S.ADDED, "external-diff.sh")))
        self.assertFalse(marker.exists())

    def test_unborn_head_has_empty_baseline(self):
        repo = make_git_repo(Path(self._tmp.name) / "unborn", {})
        write_tree(repo, {"staged.py": "x", "loose.py": "y"})
        git(repo, "add", "staged.py")
        self.assertEqual(read_git_staged_changes(repo).changes, (C(S.ADDED, "staged.py"),))
        self.assertEqual(read_git_working_tree_changes(repo).changes,
                         (C(S.ADDED, "loose.py"), C(S.ADDED, "staged.py")))
        (repo / "staged.py").unlink()
        self.assertEqual(read_git_staged_changes(repo).changes, (C(S.ADDED, "staged.py"),))
        self.assertEqual(read_git_working_tree_changes(repo).changes, (C(S.ADDED, "loose.py"),))

    def test_broken_head_is_not_treated_as_unborn(self):
        repo = make_git_repo(Path(self._tmp.name) / "broken", {})
        ref = repo / ".git" / "refs" / "heads" / "main"
        ref.write_text("0" * 40 + "\n")
        for reader in (read_git_staged_changes, read_git_working_tree_changes):
            with self.subTest(reader=reader.__name__), self.assertRaises(LocalChangeError):
                reader(repo)

    def test_central_normalization_keeps_classification_and_exclusions(self):
        write_tree(self.repo, {"src/app.py": "x", "vendor/gen.py": "# @generated\n"})
        git(self.repo, "add", "src/app.py", "vendor/gen.py")
        config = parse_config({"repo_check": {"exclude": ["vendor/"]}})
        for raw in (read_git_staged_changes(self.repo), read_git_working_tree_changes(self.repo)):
            with self.subTest(source=raw.source):
                snapshot = discover(self.repo, config, local_changes=raw)
                state = snapshot.change
                self.assertIs(state.source, raw.source)
                self.assertEqual([(c.path, c.classification.value) for c in state.changes],
                                 [("src/app.py", "source")])
                self.assertIsNone(state.requested_ref)
                self.assertIsNone(state.compare_commit)
                self.assertIsNone(state.merge_base)
                self.assertIsNone(state.head_commit)

    def test_failures_are_explicit_for_both_sources(self):
        for reader in (read_git_staged_changes, read_git_working_tree_changes):
            with self.subTest(reader=reader.__name__):
                with self.assertRaises(LocalChangeError):
                    reader(Path(self._tmp.name), git_executable="git")
                with self.assertRaisesRegex(LocalChangeError, "requires Git"):
                    reader(self.repo, git_executable=str(self.repo / "missing-git"))
                with mock.patch.object(git_client, "_invoke", side_effect=subprocess.TimeoutExpired("git", 1)):
                    with self.assertRaisesRegex(LocalChangeError, "timed out"):
                        reader(self.repo)
                original = git_client._invoke

                def failing_diff(executable, root, *args):
                    if args[0] == "diff":
                        return subprocess.CompletedProcess(args, 1, b"", b"forced failure")
                    return original(executable, root, *args)

                with mock.patch.object(git_client, "_invoke", side_effect=failing_diff):
                    with self.assertRaisesRegex(LocalChangeError, "forced failure"):
                        reader(self.repo)


class ParseUntrackedTests(unittest.TestCase):
    def test_nul_safe_paths_and_rejects_invalid(self):
        self.assertEqual(parse_untracked_paths(b"b\nq\0a\tx\0"), ("a\tx", "b\nq"))
        for output in (b"/absolute\0", b"../escape\0", b"a//b\0", b"a\0\0b\0", b"unterminated"):
            with self.subTest(output=output), self.assertRaises(LocalChangeError):
                parse_untracked_paths(output)
