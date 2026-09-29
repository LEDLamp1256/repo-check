import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_check.errors import ComparisonError
from repo_check.git import client as git_client
from repo_check.git.client import parse_name_status, read_git_comparison, scope_changes
from repo_check.model import ChangeStatus as S
from repo_check.model import GitChange as C

from support import commit_all, git, make_branch_repo, make_git_repo, requires_git, write_tree

BASE = {
    "README.md": "# r\n",
    "src/app.py": "x = 1\n",
    "src/util.py": "".join(f"line {i} of a reasonably long file for rename detection\n"
                           for i in range(20)),
    "tests/test_app.py": "def test(): pass\n",
}


def changes(repo, ref="main", root=None):
    return read_git_comparison(root or repo, ref).changes


class CompareTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def branch_repo(self, base=BASE):
        return make_branch_repo(self.tmp / "repo", base)


@requires_git
class RefResolutionTests(CompareTestCase):
    def test_branch_tag_commit_and_relative_refs(self):
        repo = self.branch_repo()
        main = git(repo, "rev-parse", "main").strip()
        git(repo, "tag", "v1", "main")
        git(repo, "tag", "-a", "v1-annotated", "-m", "annotated", "main")
        write_tree(repo, {"src/new.py": "y = 2\n"})
        head = commit_all(repo, "feature work")
        for ref in ("main", "v1", "v1-annotated", main, main[:12], "HEAD~1"):
            with self.subTest(ref=ref):
                facts = read_git_comparison(repo, ref)
                self.assertEqual(facts.requested_ref, ref)
                self.assertEqual(facts.compare_commit, main)  # annotated tag peeled
                self.assertEqual(facts.merge_base, main)
                self.assertEqual(facts.head_commit, head)
                self.assertEqual(facts.changes, (C(S.ADDED, "src/new.py"),))
                self.assertIs(type(facts.changes[0]), C)

    def test_merge_base_excludes_commits_only_on_the_compared_branch(self):
        repo = self.branch_repo()
        write_tree(repo, {"src/feature.py": "f = 1\n"})
        commit_all(repo, "feature")
        git(repo, "checkout", "-q", "main")
        write_tree(repo, {"src/main_only.py": "m = 1\n"})
        main_head = commit_all(repo, "main moves on")
        git(repo, "checkout", "-q", "feature")
        facts = read_git_comparison(repo, "main")
        self.assertEqual(facts.compare_commit, main_head)
        self.assertNotEqual(facts.merge_base, main_head)
        self.assertEqual(facts.changes, (C(S.ADDED, "src/feature.py"),))

    def test_multiple_commits_are_net_changes(self):
        repo = self.branch_repo()
        write_tree(repo, {"a.py": "1\n", "b.py": "2\n"})
        commit_all(repo, "one")
        os.remove(repo / "a.py")
        write_tree(repo, {"b.py": "3\n"})
        commit_all(repo, "two")
        self.assertEqual(changes(repo), (C(S.ADDED, "b.py"),))

    def test_no_changes(self):
        repo = self.branch_repo()
        self.assertEqual(changes(repo), ())
        self.assertEqual(changes(repo, "HEAD"), ())

    def test_uncommitted_changes_are_not_included(self):
        repo = self.branch_repo()
        write_tree(repo, {"src/committed.py": "c = 1\n"})
        commit_all(repo, "feature")
        write_tree(repo, {"src/app.py": "x = 2  # unstaged\n", "src/staged.py": "s = 1\n"})
        git(repo, "add", "src/staged.py")
        self.assertEqual(changes(repo), (C(S.ADDED, "src/committed.py"),))


@requires_git
class StatusTests(CompareTestCase):
    def test_added_modified_deleted_renamed_copied(self):
        repo = self.branch_repo()
        write_tree(repo, {"src/app.py": "x = 2\n", "docs/new.md": "new\n"})
        os.remove(repo / "README.md")
        git(repo, "mv", "tests/test_app.py", "tests/test_main.py")
        util = (repo / "src/util.py").read_text()
        write_tree(repo, {"src/util.py": util + "extra\n", "src/util_copy.py": util})
        commit_all(repo, "feature")
        self.assertEqual(
            changes(repo),
            (
                C(S.DELETED, "README.md"),
                C(S.ADDED, "docs/new.md"),
                C(S.MODIFIED, "src/app.py"),
                C(S.MODIFIED, "src/util.py"),
                C(S.COPIED, "src/util_copy.py", "src/util.py"),
                C(S.RENAMED, "tests/test_main.py", "tests/test_app.py"),
            ),
        )

    @unittest.skipIf(os.name == "nt", "symlinks")
    def test_type_change(self):
        repo = self.branch_repo()
        os.remove(repo / "src/app.py")
        os.symlink("util.py", repo / "src/app.py")
        commit_all(repo, "type change")
        self.assertEqual(changes(repo), (C(S.TYPE_CHANGED, "src/app.py"),))

    def test_unusual_file_names(self):
        repo = self.branch_repo()
        names = ["dir with space/file name.py", "lead space.py"]
        if os.name != "nt":
            names += ["tab\there.py", 'quote".py', "new\nline.py", "é.py"]
        write_tree(repo, {name: "x\n" for name in names})
        commit_all(repo, "odd names")
        self.assertEqual(changes(repo), tuple(C(S.ADDED, n) for n in sorted(names)))

    def test_deterministic_order(self):
        repo = self.branch_repo()
        # Plain code-point order: uppercase before "_" before lowercase, "." before
        # "/", and "C.py" before "b.py" (a case-insensitive sort would differ).
        # No two names differ only by case, so this also runs on case-insensitive
        # filesystems.
        write_tree(repo, {"b.py": "", "a/z.py": "", "a.txt": "", "C.py": "", "_x.py": ""})
        commit_all(repo, "many")
        expected = tuple(C(S.ADDED, p) for p in ("C.py", "_x.py", "a.txt", "a/z.py", "b.py"))
        self.assertEqual(changes(repo), expected)
        self.assertEqual(changes(repo), expected)


@requires_git
class ScopingTests(CompareTestCase):
    def test_subdirectory_scoping_and_sibling_prefixes(self):
        repo = self.branch_repo({"pkg/a.py": "a\n", "pkgs/b.py": "b\n", "top.py": "t\n"})
        write_tree(repo, {"pkg/a.py": "a2\n", "pkg/sub/c.py": "c\n", "pkgs/b.py": "b2\n",
                          "top.py": "t2\n", "pkg.py": "p\n"})
        commit_all(repo, "feature")
        self.assertEqual(
            changes(repo, root=repo / "pkg"),
            (C(S.MODIFIED, "a.py"), C(S.ADDED, "sub/c.py")),
        )
        self.assertEqual(changes(repo, root=repo / "pkgs"), (C(S.MODIFIED, "b.py"),))
        self.assertEqual(changes(repo, root=repo / "pkg" / "sub"), (C(S.ADDED, "c.py"),))

    def test_scope_uses_gits_view_of_the_root_not_its_spelling(self):
        # The prefix comes from Git, so a root spelled differently from Git's
        # own path for it still selects the right subdirectory.
        repo = self.branch_repo({"pkg/a.py": "a\n"})
        write_tree(repo, {"pkg/a.py": "a2\n"})
        commit_all(repo, "feature")
        self.assertEqual(
            changes(repo, root=repo / "pkg" / ".." / "pkg"), (C(S.MODIFIED, "a.py"),)
        )

    def test_root_in_different_letter_case_on_case_insensitive_file_system(self):
        repo = self.branch_repo({"Sources/a.swift": "a\n"})
        if not (repo / "SOURCES").exists():
            self.skipTest("file system is case-sensitive")
        write_tree(repo, {"Sources/a.swift": "a2\n"})
        commit_all(repo, "feature")
        # Path.resolve() keeps the spelling given (it does not canonicalize
        # case), while Git reports paths in their stored case.
        for spelling in ("SOURCES", "sources"):
            with self.subTest(spelling=spelling):
                root = (repo / spelling).resolve()
                self.assertEqual(changes(repo, root=root), (C(S.MODIFIED, "a.swift"),))

    def test_renames_and_copies_across_the_analysis_root(self):
        body = "".join(f"line {i} of content long enough to be detected\n" for i in range(20))
        repo = self.branch_repo(
            {"pkg/leaving.py": body, "outside/arriving.py": body + "x\n",
             "outside/source.py": body + "y\n", "pkg/src.py": body + "z\n"}
        )
        (repo / "elsewhere").mkdir()
        git(repo, "mv", "pkg/leaving.py", "elsewhere/leaving.py")
        git(repo, "mv", "outside/arriving.py", "pkg/arriving.py")
        source = (repo / "outside/source.py").read_text()
        write_tree(repo, {"outside/source.py": source + "more\n", "pkg/copied_in.py": source})
        src = (repo / "pkg/src.py").read_text()
        write_tree(repo, {"pkg/src.py": src + "more\n", "outside/copied_out.py": src})
        commit_all(repo, "cross boundary")
        whole = changes(repo)
        self.assertIn(C(S.RENAMED, "elsewhere/leaving.py", "pkg/leaving.py"), whole)
        self.assertIn(C(S.RENAMED, "pkg/arriving.py", "outside/arriving.py"), whole)
        self.assertIn(C(S.COPIED, "pkg/copied_in.py", "outside/source.py"), whole)
        self.assertIn(C(S.COPIED, "outside/copied_out.py", "pkg/src.py"), whole)
        self.assertEqual(
            changes(repo, root=repo / "pkg"),
            (
                C(S.ADDED, "arriving.py"),     # renamed in from outside
                C(S.ADDED, "copied_in.py"),    # copied in from outside
                C(S.DELETED, "leaving.py"),    # renamed out of the root
                C(S.MODIFIED, "src.py"),       # copied out: source still just modified
            ),
        )


class ScopeChangesUnitTests(unittest.TestCase):
    def test_scope_rules(self):
        entries = (
            ("M", "pkg/a.py", None),
            ("M", "pkgs/b.py", None),
            ("R", "pkg/new.py", "pkg/old.py"),
            ("R", "pkg/in.py", "out/in.py"),
            ("R", "out/gone.py", "pkg/gone.py"),
            ("C", "pkg/copy.py", "out/src.py"),
            ("C", "out/copy.py", "pkg/src.py"),
        )
        self.assertEqual(
            scope_changes(entries, "pkg"),
            (
                C(S.MODIFIED, "a.py"),
                C(S.ADDED, "copy.py"),
                C(S.DELETED, "gone.py"),
                C(S.ADDED, "in.py"),
                C(S.RENAMED, "new.py", "old.py"),
            ),
        )
        self.assertEqual(len(scope_changes(entries, "")), 7)


class ParseNameStatusTests(unittest.TestCase):
    def test_all_statuses(self):
        output = b"A\0a\0M\0m\0D\0d\0T\0t\0R087\0old\0new\0C100\0src\0dst\0"
        self.assertEqual(
            parse_name_status(output),
            (("A", "a", None), ("M", "m", None), ("D", "d", None), ("T", "t", None),
             ("R", "new", "old"), ("C", "dst", "src")),
        )

    def test_empty_output(self):
        self.assertEqual(parse_name_status(b""), ())

    def test_malformed_output_fails_safely(self):
        cases = [
            b"X\0a\0",           # unknown status
            b"U\0a\0",           # unmerged (not possible between commits)
            b"B\0a\0",           # broken pair (not requested)
            b"R\0old\0new\0",    # rename without score
            b"Mx\0a\0",          # score on single-path status
            b"A\0",              # missing path
            b"R100\0old\0",      # missing new path
            b"A\0../escape\0",   # path outside repository
            b"A\0/abs\0",
            b"A\0a//b\0",
        ]
        for output in cases:
            with self.subTest(output=output), self.assertRaises(ComparisonError):
                parse_name_status(output)


@requires_git
class ErrorTests(CompareTestCase):
    def test_missing_ref(self):
        repo = self.branch_repo()
        with self.assertRaisesRegex(
            ComparisonError, "cannot resolve comparison ref 'nope' to a local commit"
        ):
            read_git_comparison(repo, "nope")

    def test_range_and_non_commit_refs(self):
        repo = self.branch_repo()
        tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
        for ref in ("main..feature", "main...feature", tree, "HEAD:README.md", "main feature"):
            with self.subTest(ref=ref), self.assertRaisesRegex(ComparisonError, "cannot resolve"):
                read_git_comparison(repo, ref)

    def test_option_like_refs_are_rejected_before_running_git(self):
        repo = self.branch_repo()
        with mock.patch.object(git_client.subprocess, "run") as run:
            for ref in ("-x", "--output=/tmp/pwned", "--git-dir=/etc", ""):
                with self.subTest(ref=ref), self.assertRaisesRegex(ComparisonError, "invalid"):
                    read_git_comparison(repo, ref)
        run.assert_not_called()

    def test_non_git_directory(self):
        write_tree(self.tmp, {"a.py": ""})
        with self.assertRaisesRegex(ComparisonError, "is not inside a Git worktree"):
            read_git_comparison(self.tmp, "main")

    def test_missing_git_executable(self):
        with self.assertRaisesRegex(ComparisonError, "executable was not found"):
            read_git_comparison(self.tmp, "main", git_executable=str(self.tmp / "no-git"))

    def test_unrelated_histories_have_no_merge_base(self):
        repo = self.branch_repo()
        git(repo, "checkout", "-q", "--orphan", "lonely")
        git(repo, "rm", "-rfq", ".")
        write_tree(repo, {"z.py": "z\n"})
        commit_all(repo, "lonely")
        with self.assertRaisesRegex(ComparisonError, "no merge base between 'main' and HEAD"):
            read_git_comparison(repo, "main")

    def test_repository_without_commits(self):
        repo = make_git_repo(self.tmp / "empty", {})
        with self.assertRaisesRegex(ComparisonError, "cannot resolve"):
            read_git_comparison(repo, "HEAD")

    def test_timeout(self):
        timeout = subprocess.TimeoutExpired(["git"], 60)
        with mock.patch.object(git_client.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(ComparisonError, "timed out"):
                read_git_comparison(self.tmp, "main")


@requires_git
class SubmoduleTests(CompareTestCase):
    """A committed submodule pointer (gitlink) change is always a change,
    whatever Git's submodule-ignore configuration says."""

    OLD_POINTER = "1" * 40
    NEW_POINTER = "2" * 40

    def gitlink_repo(self):
        # Gitlinks are written straight into the index: no submodule clone,
        # no network, and the pointed-to commits need not exist.
        repo = make_git_repo(
            self.tmp / "repo",
            {".gitmodules": '[submodule "lib"]\n\tpath = vendor/lib\n\turl = ./lib\n'},
        )
        (repo / "vendor" / "lib").mkdir(parents=True)  # an unpopulated submodule
        self.set_pointer(repo, self.OLD_POINTER, "add submodule")
        git(repo, "checkout", "-q", "-b", "feature")
        self.set_pointer(repo, self.NEW_POINTER, "bump submodule")
        return repo

    def set_pointer(self, repo, sha, message):
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/lib")
        git(repo, "commit", "-q", "-m", message)

    def test_changed_gitlink_is_reported(self):
        repo = self.gitlink_repo()
        self.assertEqual(changes(repo), (C(S.MODIFIED, "vendor/lib"),))
        self.assertEqual(changes(repo, root=repo / "vendor"), (C(S.MODIFIED, "lib"),))

    def test_repository_and_per_submodule_ignore_config_is_overridden(self):
        repo = self.gitlink_repo()
        for key in ("diff.ignoreSubmodules", "submodule.lib.ignore"):
            with self.subTest(key=key):
                git(repo, "config", key, "all")
                self.assertEqual(git(repo, "diff", "--name-only", "main", "HEAD"), "")
                self.assertEqual(changes(repo), (C(S.MODIFIED, "vendor/lib"),))
                git(repo, "config", "--unset", key)

    def test_committed_gitmodules_ignore_is_overridden(self):
        repo = self.gitlink_repo()
        gitmodules = '[submodule "lib"]\n\tpath = vendor/lib\n\turl = ./lib\n\tignore = all\n'
        write_tree(repo, {".gitmodules": gitmodules})
        git(repo, "add", ".gitmodules")
        git(repo, "commit", "-q", "-m", "ignore submodule")
        self.assertEqual(git(repo, "diff", "--name-only", "main", "HEAD"), ".gitmodules\n")
        self.assertEqual(
            changes(repo), (C(S.MODIFIED, ".gitmodules"), C(S.MODIFIED, "vendor/lib"))
        )

    def test_environment_injected_ignore_config_is_overridden(self):
        repo = self.gitlink_repo()
        for key in ("diff.ignoreSubmodules", "submodule.lib.ignore"):
            injected = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": key,
                        "GIT_CONFIG_VALUE_0": "all"}
            with self.subTest(key=key), mock.patch.dict(os.environ, injected):
                hidden = subprocess.run(
                    ["git", "-C", str(repo), "diff", "--name-only", "main", "HEAD"],
                    capture_output=True, text=True, check=True,
                ).stdout
                self.assertEqual(hidden, "")
                self.assertEqual(changes(repo), (C(S.MODIFIED, "vendor/lib"),))


@requires_git
class ReplaceObjectsTests(CompareTestCase):
    """``git replace`` overlays are ignored: facts correspond to the commit
    objects actually named by their SHAs."""

    def history(self):
        # main: R (a.py) -> M (m.py); feature: F (f.py) on M.
        # side: X (x.py) on R, never merged; used as replacement content.
        repo = make_git_repo(self.tmp / "repo", {"a.py": "a\n"})
        root = git(repo, "rev-parse", "HEAD").strip()
        write_tree(repo, {"m.py": "m\n"})
        main = commit_all(repo, "main work")
        git(repo, "checkout", "-q", "-b", "side", root)
        write_tree(repo, {"x.py": "x\n"})
        side = commit_all(repo, "side work")
        git(repo, "checkout", "-q", "-b", "feature", main)
        write_tree(repo, {"f.py": "f\n"})
        feature = commit_all(repo, "feature work")
        return repo, root, main, side, feature

    def plain_diff(self, repo):
        return git(repo, "diff", "--name-only", "main", "HEAD").split()

    def test_head_replacement_does_not_change_facts(self):
        repo, root, main, side, feature = self.history()
        git(repo, "replace", feature, side)
        self.assertEqual(self.plain_diff(repo), ["m.py", "x.py"])  # Git honors it
        facts = read_git_comparison(repo, "main")
        self.assertEqual(facts.head_commit, feature)
        self.assertEqual(facts.merge_base, main)
        self.assertEqual(facts.changes, (C(S.ADDED, "f.py"),))

    def test_comparison_side_replacement_does_not_change_facts(self):
        repo, root, main, side, feature = self.history()
        git(repo, "replace", main, side)
        self.assertEqual(self.plain_diff(repo), ["f.py", "m.py", "x.py"])
        facts = read_git_comparison(repo, "main")
        self.assertEqual((facts.compare_commit, facts.merge_base), (main, main))
        self.assertEqual(facts.changes, (C(S.ADDED, "f.py"),))

    def test_replacement_graft_does_not_change_merge_base(self):
        repo, root, main, side, feature = self.history()
        git(repo, "replace", "--graft", feature, root)  # pretend F's parent is R
        self.assertEqual(git(repo, "merge-base", "main", "HEAD").strip(), root)
        facts = read_git_comparison(repo, "main")
        self.assertEqual(facts.merge_base, main)
        self.assertEqual(facts.changes, (C(S.ADDED, "f.py"),))

    def test_config_and_environment_cannot_reenable_replacements(self):
        repo, root, main, side, feature = self.history()
        git(repo, "replace", feature, side)
        git(repo, "config", "core.useReplaceRefs", "true")
        expected = (C(S.ADDED, "f.py"),)
        self.assertEqual(changes(repo), expected)
        for variables in (
            {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.useReplaceRefs",
             "GIT_CONFIG_VALUE_0": "true"},
            {"GIT_CONFIG_PARAMETERS": "'core.usereplacerefs'='true'"},
            {"GIT_REPLACE_REF_BASE": "refs/replace/"},
            {"GIT_NO_REPLACE_OBJECTS": "0"},
        ):
            with self.subTest(variables=sorted(variables)), \
                    mock.patch.dict(os.environ, variables):
                self.assertEqual(changes(repo), expected)
                self.assertEqual(read_git_comparison(repo, "main").head_commit, feature)

    def test_git_state_is_unaffected_by_replacements(self):
        repo, root, main, side, feature = self.history()
        before = git_client.read_git_state(repo)
        git(repo, "replace", feature, side)
        after = git_client.read_git_state(repo)
        self.assertEqual(after, before)
        self.assertEqual(after.tracked_paths, ("a.py", "f.py", "m.py"))


@requires_git
class HardeningTests(CompareTestCase):
    def test_every_invocation_is_hardened(self):
        repo = self.branch_repo()
        write_tree(repo, {"n.py": ""})
        commit_all(repo)
        real_run = subprocess.run
        calls = []

        def spy(args, **kwargs):
            calls.append((args, kwargs))
            return real_run(args, **kwargs)

        with mock.patch.object(git_client.subprocess, "run", side_effect=spy):
            facts = read_git_comparison(repo, "main")
        self.assertEqual(facts.changes, (C(S.ADDED, "n.py"),))
        commands = [args[6] for args, _ in calls]
        self.assertEqual(
            commands, ["rev-parse", "rev-parse", "rev-parse", "rev-parse", "merge-base", "diff"]
        )
        self.assertEqual(calls[0][0][6:], ["rev-parse", "--show-toplevel"])
        self.assertEqual(calls[1][0][6:], ["rev-parse", "--show-prefix"])
        for args, kwargs in calls:
            self.assertIsInstance(args, list)
            self.assertEqual(
                args[:6],
                ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(repo)],
            )
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(kwargs["timeout"], git_client.GIT_TIMEOUT_SECONDS)
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
        # The user's ref appears only inside the rev-parse --verify argument;
        # merge-base and diff use resolved object names.
        self.assertEqual(calls[2][0][6:], ["rev-parse", "--verify", "--quiet", "main^{commit}"])
        self.assertTrue(all("main" not in a for a in calls[4][0][7:] + calls[5][0][7:]))
        diff_args = calls[5][0]
        self.assertEqual(diff_args[-1], "--")
        for flag in ("--name-status", "-z", "--find-renames", "--find-copies", "--no-ext-diff",
                     "--ignore-submodules=none"):
            self.assertIn(flag, diff_args)

    def test_ambient_git_redirect_variables_are_ignored(self):
        repo = self.branch_repo()
        write_tree(repo, {"mine.py": ""})
        commit_all(repo)
        other = make_branch_repo(self.tmp / "other", {"x.py": "x\n"})
        write_tree(other, {"theirs.py": ""})
        commit_all(other)
        hostile = {
            "GIT_DIR": str(other / ".git"),
            "GIT_WORK_TREE": str(other),
            "GIT_INDEX_FILE": str(other / ".git" / "index"),
            "GIT_OBJECT_DIRECTORY": str(other / ".git" / "objects"),
            "GIT_COMMON_DIR": str(other / ".git"),
        }
        with mock.patch.dict(os.environ, hostile):
            self.assertEqual(changes(repo), (C(S.ADDED, "mine.py"),))

    @unittest.skipIf(os.name == "nt", "uses a POSIX shell script as the hook")
    def test_repository_fsmonitor_hook_is_not_executed(self):
        repo = self.branch_repo()
        write_tree(repo, {"n.py": ""})
        commit_all(repo)
        marker = self.tmp / "hook-ran"
        hook = self.tmp / "hook.sh"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nprintf '/\\0'\n", encoding="utf-8")
        hook.chmod(0o755)
        git(repo, "config", "core.fsmonitor", str(hook))
        git(repo, "status", "--porcelain")
        if not marker.exists():
            self.skipTest("this git version does not invoke core.fsmonitor hooks")
        marker.unlink()
        self.assertEqual(changes(repo), (C(S.ADDED, "n.py"),))
        self.assertFalse(marker.exists())

    @unittest.skipIf(os.name == "nt", "uses a POSIX shell script as the hook")
    def test_environment_injected_fsmonitor_hook_is_not_executed(self):
        # GIT_CONFIG_COUNT / GIT_CONFIG_PARAMETERS are overridden by the
        # command line's -c core.fsmonitor=false, for Git state and comparison.
        repo = self.branch_repo()
        write_tree(repo, {"n.py": ""})
        commit_all(repo)
        marker = self.tmp / "hook-ran"
        hook = self.tmp / "hook.sh"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nprintf '/\\0'\n", encoding="utf-8")
        hook.chmod(0o755)
        injected = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": str(hook),
        }
        with mock.patch.dict(os.environ, injected):
            subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                           capture_output=True, check=False)
        if not marker.exists():
            self.skipTest("this git version does not invoke core.fsmonitor hooks")
        marker.unlink()
        for variables in (injected, {"GIT_CONFIG_PARAMETERS": f"'core.fsmonitor'='{hook}'"}):
            with self.subTest(variables=sorted(variables)), \
                    mock.patch.dict(os.environ, variables):
                self.assertEqual(changes(repo), (C(S.ADDED, "n.py"),))
                self.assertTrue(git_client.read_git_state(repo).available)
                self.assertFalse(marker.exists())

    def test_user_diff_config_does_not_change_facts(self):
        repo = self.branch_repo()
        git(repo, "mv", "src/util.py", "src/tools.py")
        commit_all(repo, "rename")
        for key, value in (("diff.renames", "false"), ("diff.relative", "true"),
                           ("diff.external", "false"), ("diff.noprefix", "true")):
            git(repo, "config", key, value)
        self.assertEqual(changes(repo), (C(S.RENAMED, "src/tools.py", "src/util.py"),))
        self.assertEqual(
            changes(repo, root=repo / "src"), (C(S.RENAMED, "tools.py", "util.py"),)
        )


if __name__ == "__main__":
    unittest.main()
