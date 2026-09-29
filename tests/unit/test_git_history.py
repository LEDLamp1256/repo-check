import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_check.config import parse_config
from repo_check.discovery import discover, normalize_history
from repo_check.engine import analyze
from repo_check.errors import HistoryError
from repo_check.git import client as git_client
from repo_check.git.client import parse_history_log, read_git_history, scope_history
from repo_check.model import HistoryCommit as H
from repo_check.model import HistoryState

from support import commit_all, git, make_git_repo, requires_git, write_tree

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
RAW_ADD = b":000000 100644 " + b"0" * 40 + b" " + b"1" * 40 + b" A"
RAW_MOD = b":100644 100644 " + b"1" * 40 + b" " + b"2" * 40 + b" M"


def log_output(*commits):
    """Build ``git log -z --raw --format=%H`` output: (sha, [(raw, path)])."""
    out = b""
    for sha, entries in commits:
        out += sha.encode() + b"\0"
        for index, (raw, path) in enumerate(entries):
            out += (b"\n" if index == 0 else b"") + raw + b"\0" + path + b"\0"
    return out


class ParseHistoryLogTests(unittest.TestCase):
    def test_commits_in_output_order_with_sorted_unique_paths(self):
        output = log_output(
            (SHA_A, [(RAW_MOD, b"z.py"), (RAW_ADD, b"a.py")]),
            (SHA_B, []),  # e.g. an empty commit
            (SHA_C, [(RAW_ADD, b"m.py")]),
        )
        self.assertEqual(
            parse_history_log(output),
            (H(SHA_A, ("a.py", "z.py")), H(SHA_B, ()), H(SHA_C, ("m.py",))),
        )

    def test_duplicate_paths_collapse(self):
        # A type change can appear as a delete and an add of the same path.
        raw_del = b":100644 000000 " + b"1" * 40 + b" " + b"0" * 40 + b" D"
        output = log_output((SHA_A, [(raw_del, b"p"), (RAW_ADD, b"p")]))
        self.assertEqual(parse_history_log(output), (H(SHA_A, ("p",)),))

    def test_nul_safe_unusual_paths(self):
        paths = [b"tab\there", b"new\nline", b"sp ace", b"quote\"s", b"back\\slash",
                 b"\xc3\xa9", b"\xff\xfe", b":colon-first", b"\n:looks-like-raw", SHA_B.encode()]
        output = log_output((SHA_A, [(RAW_ADD, path) for path in paths]))
        self.assertEqual(
            parse_history_log(output),
            (H(SHA_A, tuple(sorted(os.fsdecode(p) for p in paths))),),
        )

    def test_empty_output(self):
        self.assertEqual(parse_history_log(b""), ())

    def test_sha256_object_names(self):
        self.assertEqual(parse_history_log(log_output(("e" * 64, []))), (H("e" * 64, ()),))

    def test_unexpected_output_is_an_error(self):
        for output in (
            b"not-a-sha\0",
            RAW_ADD + b"\0a.py\0",  # an entry before any commit
            log_output((SHA_A, [])) + RAW_ADD + b"\0",  # entry without a path
            SHA_A.encode() + b"\0\n:100644 100644 x y R100\0a\0b\0",  # rename status
            log_output((SHA_A, [(RAW_ADD, b"../escape")])),
            log_output((SHA_A, [(RAW_ADD, b"/abs")])),
            log_output((SHA_A, [(RAW_ADD, b"a//b")])),
            SHA_A[:12].encode() + b"\0",  # abbreviated
        ):
            with self.subTest(output=output), self.assertRaises(HistoryError):
                parse_history_log(output)


class ScopeHistoryTests(unittest.TestCase):
    def test_root_prefix_keeps_everything(self):
        commits = (H(SHA_A, ("a", "pkg/b")),)
        self.assertIs(scope_history(commits, ""), commits)

    def test_paths_are_relative_to_the_prefix_and_commits_are_kept(self):
        commits = (
            H(SHA_A, ("pkg/a.py", "pkg/sub/b.py", "pkgs/c.py", "pkg", "top.py")),
            H(SHA_B, ("other/x.py",)),
        )
        self.assertEqual(
            scope_history(commits, "pkg"),
            (H(SHA_A, ("a.py", "sub/b.py")), H(SHA_B, ())),
        )


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def linear_repo(self, count=3, name="repo"):
        """A repository with ``count`` commits, each adding f<i>.py and
        touching shared.py; returns (repo, [sha oldest..newest])."""
        repo = make_git_repo(self.tmp / name, {"shared.py": "0\n", "f0.py": ""})
        shas = [git(repo, "rev-parse", "HEAD").strip()]
        for i in range(1, count):
            write_tree(repo, {"shared.py": f"{i}\n", f"f{i}.py": ""})
            shas.append(commit_all(repo, f"c{i}"))
        return repo, shas


@requires_git
class ReadGitHistoryTests(HistoryTestCase):
    def test_topo_order_with_touched_paths_and_head_anchor(self):
        repo, shas = self.linear_repo(3)
        history = read_git_history(repo, 10)
        self.assertEqual(
            history,
            HistoryState(
                requested_commits=10,
                head_commit=shas[2],
                commits=(
                    H(shas[2], ("f2.py", "shared.py")),
                    H(shas[1], ("f1.py", "shared.py")),
                    H(shas[0], ("f0.py", "shared.py")),  # root commit against empty tree
                ),
                is_shallow_repository=False,
            ),
        )

    def test_depth_is_respected(self):
        repo, shas = self.linear_repo(5)
        for depth in (1, 2, 5, 6, 1000):
            with self.subTest(depth=depth):
                history = read_git_history(repo, depth)
                self.assertEqual(history.requested_commits, depth)
                self.assertEqual([c.commit for c in history.commits],
                                 list(reversed(shas))[:depth])

    def test_invalid_depth(self):
        repo, _ = self.linear_repo(1)
        with mock.patch.object(git_client.subprocess, "run") as run:
            for depth in (0, -1, True, 1.5, "3", None):
                with self.subTest(depth=depth), self.assertRaisesRegex(HistoryError, "positive"):
                    read_git_history(repo, depth)
        run.assert_not_called()

    def test_merge_commits_are_excluded_and_order_is_topological(self):
        repo, shas = self.linear_repo(1)
        git(repo, "checkout", "-q", "-b", "side")
        write_tree(repo, {"side.py": ""})
        side = commit_all(repo, "side")
        git(repo, "checkout", "-q", "main")
        write_tree(repo, {"main.py": ""})
        main = commit_all(repo, "main")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "-q", "--no-ff",
            "-m", "merge", "side")
        merge = git(repo, "rev-parse", "HEAD").strip()
        history = read_git_history(repo, 10)
        names = [c.commit for c in history.commits]
        # HEAD is the merge: it anchors the query but is not itself listed.
        self.assertEqual(history.head_commit, merge)
        self.assertEqual(len(merge), 40)
        self.assertNotIn(merge, names)
        self.assertEqual(sorted(names), sorted([side, main, shas[0]]))
        self.assertEqual(names[-1], shas[0])  # no parent before its children
        expected = git(repo, "log", "--format=%H", "--topo-order", "--no-merges", "HEAD").split()
        self.assertEqual(names, expected)
        # Merges do not count toward the depth, and the anchor is unchanged.
        limited = read_git_history(repo, 2)
        self.assertEqual(len(limited.commits), 2)
        self.assertEqual(limited.head_commit, merge)
        self.assertNotIn(merge, [c.commit for c in limited.commits])
        # The anchor survives subdirectory analysis and exclusions.
        write_tree(repo, {"sub/.keep": ""})
        git(repo, "add", "sub/.keep")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
            "--amend", "--no-edit")  # keep HEAD a merge, now with sub/
        merge = git(repo, "rev-parse", "HEAD").strip()
        scoped = read_git_history(repo / "sub", 10)
        self.assertEqual(scoped.head_commit, merge)
        self.assertNotIn(merge, [c.commit for c in scoped.commits])
        self.assertEqual(normalize_history(history, ("*.py",)).head_commit, history.head_commit)

    def test_rename_touches_old_and_new_path_without_following(self):
        repo, _ = self.linear_repo(1)
        write_tree(repo, {"body.py": "".join(f"line {i}\n" for i in range(30))})
        commit_all(repo, "add body")
        git(repo, "mv", "body.py", "moved.py")
        rename = commit_all(repo, "rename")
        git(repo, "config", "diff.renames", "copies")
        history = read_git_history(repo, 1)
        self.assertEqual(history.commits, (H(rename, ("body.py", "moved.py")),))

    def test_empty_commit_is_kept_with_no_paths(self):
        repo, shas = self.linear_repo(1)
        empty = commit_all(repo, "empty")  # commit_all allows empty commits
        self.assertEqual(read_git_history(repo, 1).commits, (H(empty, ()),))

    def test_unusual_paths_round_trip(self):
        names = ["tab\there.py", "new\nline.py", "sp ace.py", "ünï.py", "quote\"d.py",
                 "-dash.py", ":colon.py", "a" * 40]
        repo = make_git_repo(self.tmp / "repo", {})
        write_tree(repo, {name: "" for name in names})
        commit_all(repo, "unusual")  # add -A: ':colon.py' is not pathspec magic here
        history = read_git_history(repo, 1)
        self.assertEqual(history.commits[0].touched_paths, tuple(sorted(names)))

    def test_subdirectory_scoping(self):
        repo = make_git_repo(self.tmp / "repo", {"pkg/a.py": "", "pkgs/b.py": "", "top.py": ""})
        write_tree(repo, {"other/x.py": ""})
        outside = commit_all(repo, "outside only")
        write_tree(repo, {"pkg/sub/c.py": "", "top.py": "changed\n"})
        both = commit_all(repo, "both")
        root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
        history = read_git_history(repo / "pkg", 10)
        self.assertEqual(
            history.commits,
            (H(both, ("sub/c.py",)), H(outside, ()), H(root, ("a.py",))),
        )
        self.assertEqual(history.head_commit, both)
        # A deeper root sees only its own subtree, from the same anchor.
        deeper = read_git_history(repo / "pkg" / "sub", 10)
        self.assertEqual(deeper.commits[0], H(both, ("c.py",)))
        self.assertEqual(deeper.head_commit, both)

    def test_shallow_repository_is_reported_not_an_error(self):
        repo, shas = self.linear_repo(4)
        clone = self.tmp / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "2", f"file://{repo}", str(clone)],
                       check=True, capture_output=True)
        history = read_git_history(clone, 10)
        self.assertTrue(history.is_shallow_repository)
        self.assertEqual(history.head_commit, shas[3])
        self.assertEqual([c.commit for c in history.commits], [shas[3], shas[2]])
        self.assertFalse(read_git_history(repo, 10).is_shallow_repository)

    def test_deterministic(self):
        repo, _ = self.linear_repo(6)
        first = read_git_history(repo, 4)
        for _ in range(3):
            self.assertEqual(read_git_history(repo, 4), first)
        # Repeatable even though every fixture commit has the same author and
        # committer date.
        self.assertEqual(len({c.commit for c in first.commits}), 4)


@requires_git
class HistoryErrorTests(HistoryTestCase):
    def test_non_git_directory(self):
        write_tree(self.tmp, {"a.py": ""})
        with self.assertRaisesRegex(HistoryError, "is not inside a Git worktree"):
            read_git_history(self.tmp, 5)

    def test_repository_without_commits(self):
        repo = make_git_repo(self.tmp / "empty", {})
        with self.assertRaisesRegex(HistoryError, "cannot resolve HEAD to a commit"):
            read_git_history(repo, 5)

    def test_missing_git_executable(self):
        with self.assertRaisesRegex(HistoryError, "--history requires Git"):
            read_git_history(self.tmp, 5, git_executable=str(self.tmp / "no-git"))

    def test_timeout(self):
        timeout = subprocess.TimeoutExpired(["git"], 60)
        with mock.patch.object(git_client.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(HistoryError, "timed out"):
                read_git_history(self.tmp, 5)

    def test_git_log_failure(self):
        repo, _ = self.linear_repo(2)
        real_run = subprocess.run

        def failing_log(args, **kwargs):
            if "log" in args:
                return subprocess.CompletedProcess(args, 128, b"", b"fatal: broken\n")
            return real_run(args, **kwargs)

        with mock.patch.object(git_client.subprocess, "run", side_effect=failing_log):
            with self.assertRaisesRegex(HistoryError, "git log failed: fatal: broken"):
                read_git_history(repo, 5)


@requires_git
class HistoryHardeningTests(HistoryTestCase):
    def test_every_invocation_is_hardened_and_local(self):
        repo, shas = self.linear_repo(2)
        real_run = subprocess.run
        calls = []

        def spy(args, **kwargs):
            calls.append((args, kwargs))
            return real_run(args, **kwargs)

        with mock.patch.object(git_client.subprocess, "run", side_effect=spy):
            read_git_history(repo, 7)
        self.assertEqual(
            [args[6:] if args[6] == "rev-parse" else args[6] for args, _ in calls],
            [["rev-parse", "--show-toplevel"], ["rev-parse", "--show-prefix"],
             ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
             ["rev-parse", "--is-shallow-repository"], "log"],
        )
        for args, kwargs in calls:
            self.assertEqual(
                args[:6],
                ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(repo)],
            )
            self.assertIs(kwargs["shell"], False)
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["timeout"], git_client.GIT_TIMEOUT_SECONDS)
            self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
            for forbidden in ("fetch", "pull", "clone", "ls-remote", "remote", "push"):
                self.assertNotIn(forbidden, args)
        log_args = calls[-1][0]
        self.assertEqual(log_args[-3:], ["--max-count=7", shas[-1], "--"])  # resolved HEAD
        for flag in ("-z", "--raw", "--format=%H", "--topo-order", "--no-merges", "--root",
                     "--no-renames", "--no-relative", "--no-show-signature", "--no-ext-diff"):
            self.assertIn(flag, log_args)

    def test_hostile_repository_config_does_not_change_results(self):
        repo, _ = self.linear_repo(3)
        git(repo, "mv", "f1.py", "g1.py")
        commit_all(repo, "rename")
        expected = read_git_history(repo, 10)
        for key, value in (
            ("log.showRoot", "false"), ("log.showSignature", "true"), ("diff.renames", "copies"),
            ("diff.relative", "true"), ("diff.noprefix", "true"), ("color.ui", "always"),
            ("color.diff", "always"), ("log.abbrevCommit", "true"), ("core.abbrev", "7"),
            ("format.pretty", "oneline"), ("diff.ignoreSubmodules", "all"),
            ("log.decorate", "full"), ("core.quotePath", "true"), ("diff.external", "false"),
        ):
            git(repo, "config", key, value)
        self.assertEqual(read_git_history(repo, 10), expected)
        self.assertEqual(read_git_history(repo, 10).commits[-1].touched_paths,
                         ("f0.py", "shared.py"))  # root commit still diffed

    def test_injected_config_and_redirect_environment_are_ignored(self):
        repo, _ = self.linear_repo(3)
        expected = read_git_history(repo, 10)
        other, _ = self.linear_repo(2, name="other")
        for variables in (
            {"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "log.showRoot",
             "GIT_CONFIG_VALUE_0": "false", "GIT_CONFIG_KEY_1": "diff.relative",
             "GIT_CONFIG_VALUE_1": "true"},
            {"GIT_CONFIG_PARAMETERS": "'log.showroot'='false' 'color.ui'='always'"},
            {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other),
             "GIT_OBJECT_DIRECTORY": str(other / ".git" / "objects")},
        ):
            with self.subTest(variables=sorted(variables)), \
                    mock.patch.dict(os.environ, variables):
                self.assertEqual(read_git_history(repo, 10), expected)

    def test_replacement_objects_are_ignored(self):
        repo, shas = self.linear_repo(3)
        expected = read_git_history(repo, 10)
        git(repo, "replace", "--graft", shas[2], shas[0])  # hide shas[1]
        git(repo, "replace", shas[0], shas[1])
        self.assertNotEqual(git(repo, "log", "--format=%H").split(), [c.commit for c in
                                                                     expected.commits])
        self.assertEqual(read_git_history(repo, 10), expected)
        with mock.patch.dict(os.environ, {"GIT_NO_REPLACE_OBJECTS": "0",
                                          "GIT_REPLACE_REF_BASE": "refs/replace/"}):
            self.assertEqual(read_git_history(repo, 10), expected)


@requires_git
class HistorySnapshotTests(HistoryTestCase):
    def test_exclusions_are_applied_centrally(self):
        history = HistoryState(
            3, SHA_C, (H(SHA_A, ("keep.py", "vendor/x.py")), H(SHA_B, ("gen.min.js",))), True
        )
        config = parse_config({"repo_check": {"exclude": ["vendor/", "*.min.js"]}})
        self.assertEqual(
            normalize_history(history, config.exclude),
            HistoryState(3, SHA_C, (H(SHA_A, ("keep.py",)), H(SHA_B, ())), True),
        )
        self.assertIs(normalize_history(history, ()), history)
        write_tree(self.tmp, {"a.py": ""})
        self.assertEqual(discover(self.tmp, config, history=history).history,
                         normalize_history(history, config.exclude))
        self.assertIsNone(discover(self.tmp, config).history)

    def test_analyze_attaches_history_only_when_requested(self):
        repo, shas = self.linear_repo(2)
        write_tree(repo, {"vendor/lib.py": "", "repo-check.toml":
                          '[repo_check]\nexclude = ["vendor/"]\n'})
        vendored = commit_all(repo, "vendor")
        plain = analyze(repo)
        self.assertIsNone(plain.snapshot.history)
        with_history = analyze(repo, history_commits=5)
        self.assertEqual(
            with_history.snapshot.history,
            HistoryState(5, vendored, (H(vendored, ("repo-check.toml",)),
                                       H(shas[1], ("f1.py", "shared.py")),
                                       H(shas[0], ("f0.py", "shared.py")))),
        )
        # Every other snapshot fact and the findings are unchanged.
        for field in ("files", "git", "directories", "unindexed_paths", "change"):
            self.assertEqual(getattr(with_history.snapshot, field), getattr(plain.snapshot, field))
        self.assertEqual(with_history.findings, plain.findings)

    def test_history_is_relative_to_the_analysis_root(self):
        repo = make_git_repo(self.tmp / "repo", {"pkg/a.py": "", "b.py": ""})
        result = analyze(repo / "pkg", history_commits=1)
        self.assertEqual(result.snapshot.history.commits[0].touched_paths, ("a.py",))

    def test_history_composes_with_compare(self):
        repo, shas = self.linear_repo(2)
        git(repo, "checkout", "-q", "-b", "feature")
        write_tree(repo, {"feature.py": ""})
        feature = commit_all(repo, "feature")
        result = analyze(repo, compare_ref="main", history_commits=2)
        self.assertEqual([c.path for c in result.snapshot.change.changes], ["feature.py"])
        self.assertEqual([c.commit for c in result.snapshot.history.commits], [feature, shas[1]])
        self.assertEqual(result.findings, analyze(repo, compare_ref="main").findings)


if __name__ == "__main__":
    unittest.main()
