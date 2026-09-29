"""The only place RepoCheck runs Git.

Git is optional for ordinary analysis. Any failure to obtain usable state
(no ``git`` executable, not a worktree, Git error, timeout) yields
``GitState.unavailable()`` rather than an error, so analysis that does not
depend on Git is unaffected.

An explicit comparison (``--compare REF``) is different: ``read_git_comparison``
raises ``ComparisonError`` for every failure, because silently reporting no
change findings would misrepresent the request. An explicit history request
(``--history N``) likewise makes ``read_git_history`` raise ``HistoryError``.
Explicit local change acquisition raises ``LocalChangeError`` on failure.

Only machine-readable output is used: ``git rev-parse``, ``git merge-base``,
NUL-delimited ``git ls-files -z``, ``git diff --name-status -z`` and
``git log -z --raw``. Commands
run without a shell, with a timeout, stdin disconnected, repository-redirecting
environment variables removed, ``git replace`` overlays ignored, and
``core.fsmonitor`` disabled. Nothing fetches or contacts a remote.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

from repo_check.errors import ComparisonError, HistoryError, LocalChangeError, RepoCheckError
from repo_check.model import (
    ChangeSource,
    ChangeStatus,
    GitChange,
    GitComparison,
    GitLocalChanges,
    GitState,
    HistoryCommit,
    HistoryState,
)

GIT_TIMEOUT_SECONDS = 60

# Environment variables that would point Git at a different repository or
# index than the one containing the analysis root. They are removed so the
# result depends only on the analyzed path.
_REPOSITORY_ENV_VARS = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    }
)


# Global options for every Git command:
# * --no-replace-objects: ``git replace`` overlays are ignored, so facts
#   (resolved commits, merge base, changed paths) correspond to the actual
#   objects named by their SHAs, whatever core.useReplaceRefs or
#   GIT_REPLACE_REF_BASE say.
# * core.fsmonitor=false: repository-local config must not be able to make a
#   read-only query run commands (core.fsmonitor names a program Git may
#   execute when reading the index, which ls-files does).
_GLOBAL_OPTIONS = ("--no-replace-objects", "-c", "core.fsmonitor=false")


def read_git_state(root: Path, git_executable: str = "git") -> GitState:
    """Return normalized Git state for the analysis directory ``root``."""
    toplevel = _run_git(git_executable, root, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return GitState.unavailable()
    repository_root = os.fsdecode(toplevel).rstrip("\n")
    if not repository_root:
        return GitState.unavailable()

    listing = _run_git(git_executable, root, "ls-files", "-z")
    if listing is None:
        return GitState.unavailable()

    return GitState(
        available=True,
        repository_root=Path(repository_root),
        tracked_paths=parse_ls_files(listing),
    )


def parse_ls_files(output: bytes) -> tuple[str, ...]:
    """Parse ``git ls-files -z`` output into sorted, unique relative paths.

    Entries that are not plain relative paths beneath the analysis root
    (absolute, or containing ``.``/``..`` components) are dropped.
    """
    paths = set()
    for raw in output.split(b"\0"):
        if not raw:
            continue
        path = os.fsdecode(raw)
        if any(part in ("", ".", "..") for part in path.split("/")):
            continue  # absolute ("/x" has an empty first part), "a//b", "../x"
        paths.add(path)
    return tuple(sorted(paths))


# A full commit object name: SHA-1 (40) or SHA-256 (64) hex digits.
_OBJECT_NAME = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

# The diff flags used for comparison and local change acquisition. Rename and
# copy detection are explicit (not left to user config), with Git's default
# rename limit pinned.
# Changed submodule pointers (gitlinks) are always reported, whatever
# diff.ignoreSubmodules or submodule.<name>.ignore say.
_DIFF_ARGS = (
    "diff",
    "--name-status",
    "-z",
    "--find-renames",
    "--find-copies",
    "-l1000",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--ignore-submodules=none",
)

_SINGLE_PATH_STATUS = {
    "A": ChangeStatus.ADDED,
    "M": ChangeStatus.MODIFIED,
    "D": ChangeStatus.DELETED,
    "T": ChangeStatus.TYPE_CHANGED,
}
_TWO_PATH_STATUS = {"R": ChangeStatus.RENAMED, "C": ChangeStatus.COPIED}


def read_git_comparison(root: Path, ref: str, git_executable: str = "git") -> GitComparison:
    """Committed changes from ``merge-base(ref, HEAD)`` to ``HEAD``, scoped to
    the analysis directory ``root``. Raises ``ComparisonError`` on any failure.

    Only committed history is compared; the working tree and index are not.
    ``ref`` must resolve to a commit locally; nothing is fetched.
    """
    if not ref or ref.startswith("-"):
        raise ComparisonError(
            f"invalid comparison ref {ref!r}: a ref must be non-empty and must not start with '-'"
        )
    toplevel_output = _git_or_raise(
        git_executable, root, ("rev-parse", "--show-toplevel"),
        f"{root} is not inside a Git worktree",
    )
    toplevel = Path(os.fsdecode(toplevel_output).rstrip("\n"))
    # The analysis root's location inside the worktree, as Git sees it. Git's
    # spelling can differ from ``root``'s (letter case on case-insensitive
    # file systems, Unicode normalization on macOS), and diff paths use Git's.
    prefix_output = _git_or_raise(
        git_executable, root, ("rev-parse", "--show-prefix"),
        f"cannot locate {root} within Git worktree {toplevel}",
    )
    prefix = os.fsdecode(prefix_output).removesuffix("\n").removesuffix("/")

    compare_commit = _resolve_commit(
        git_executable, root, ref, f"cannot resolve comparison ref {ref!r} to a local commit"
    )
    head_commit = _resolve_commit(
        git_executable, root, "HEAD", "cannot resolve HEAD to a commit"
    )
    merge_base = _single_object_name(
        _git_or_raise(
            git_executable, root, ("merge-base", compare_commit, head_commit),
            f"no merge base between {ref!r} and HEAD",
        ),
        f"no single merge base between {ref!r} and HEAD",
    )
    diff = _git_or_raise(
        git_executable, toplevel, (*_DIFF_ARGS, merge_base, head_commit, "--"),
        f"git diff failed for {ref!r}",
    )
    return GitComparison(
        requested_ref=ref,
        compare_commit=compare_commit,
        merge_base=merge_base,
        head_commit=head_commit,
        changes=scope_changes(parse_name_status(diff), prefix),
    )


def read_git_staged_changes(root: Path, git_executable: str = "git") -> GitLocalChanges:
    """Read the index relative to HEAD (or the empty tree on an unborn branch)."""
    toplevel, prefix, head = _local_context(root, git_executable, "staged")
    revision = (head,) if head is not None else ()
    diff = _git_or_raise(
        git_executable, toplevel,
        (*_DIFF_ARGS, "--cached", *revision, "--"),
        "cannot read staged changes", LocalChangeError, "staged change analysis",
    )
    return GitLocalChanges(
        source=ChangeSource.STAGED,
        changes=scope_changes(parse_name_status(diff, LocalChangeError), prefix),
    )


def read_git_working_tree_changes(root: Path, git_executable: str = "git") -> GitLocalChanges:
    """Read the current working copy relative to HEAD, including untracked files.

    A single HEAD-to-worktree diff describes the net tracked state, including
    staged and unstaged changes to the same path. On an unborn branch every
    existing index path is an addition; index-to-worktree deletions remove it.
    Git supplies non-ignored untracked paths separately.
    """
    toplevel, prefix, head = _local_context(root, git_executable, "working-tree")
    if head is not None:
        diff = _git_or_raise(
            git_executable, toplevel, (*_DIFF_ARGS, head, "--"),
            "cannot read working-tree changes", LocalChangeError, "working-tree change analysis",
        )
        entries = parse_name_status(diff, LocalChangeError)
    else:
        staged = _git_or_raise(
            git_executable, toplevel, (*_DIFF_ARGS, "--cached", "--"),
            "cannot read staged changes", LocalChangeError, "working-tree change analysis",
        )
        unstaged = _git_or_raise(
            git_executable, toplevel, (*_DIFF_ARGS, "--"),
            "cannot read working-tree changes", LocalChangeError, "working-tree change analysis",
        )
        indexed = {path for _, path, _ in parse_name_status(staged, LocalChangeError)}
        for status, path, _ in parse_name_status(unstaged, LocalChangeError):
            if status == "D":
                indexed.discard(path)
            else:
                indexed.add(path)
        entries = tuple(("A", path, None) for path in indexed)

    untracked = _git_or_raise(
        git_executable, toplevel,
        ("ls-files", "--others", "--exclude-standard", "--full-name", "-z"),
        "cannot read untracked files", LocalChangeError, "working-tree change analysis",
    )
    changes = {change.path: change for change in scope_changes(entries, prefix)}
    for path in parse_untracked_paths(untracked):
        for change in scope_changes((("A", path, None),), prefix):
            previous = changes.get(change.path)
            if previous is None:
                changes[change.path] = change
            elif previous.status is ChangeStatus.DELETED:
                # A path removed from the index but recreated in the worktree
                # must be compared with HEAD as it exists now.
                if head is None:
                    raise LocalChangeError(f"contradictory Git changes for {change.path!r}")
                replacement = _recreated_change(toplevel, head, path, git_executable)
                if replacement is None:
                    del changes[change.path]
                else:
                    changes[change.path] = GitChange(replacement, change.path)
            else:
                raise LocalChangeError(f"contradictory Git changes for {change.path!r}")
    return GitLocalChanges(
        source=ChangeSource.WORKING_TREE,
        changes=tuple(sorted(changes.values(), key=lambda c: (c.path, c.old_path or "", c.status.value))),
    )


def _recreated_change(
    toplevel: Path, head: str, path: str, git_executable: str
) -> ChangeStatus | None:
    """Compare raw current regular-file bytes and executable mode with HEAD.

    This exceptional path handles a file deleted from the index and recreated
    as untracked. Raw hashing avoids attribute-selected clean filters.
    """
    entry = _git_or_raise(
        git_executable, toplevel, ("ls-tree", "-z", head, "--", path),
        f"cannot inspect HEAD path {path!r}", LocalChangeError, "working-tree change analysis",
    )
    fields = entry.removesuffix(b"\0").split(b"\0")
    if len(fields) != 1 or b"\t" not in fields[0]:
        raise LocalChangeError(f"unexpected HEAD tree entry for {path!r}")
    metadata, found_path = fields[0].split(b"\t", 1)
    parts = metadata.split(b" ")
    if len(parts) != 3 or os.fsdecode(found_path) != path:
        raise LocalChangeError(f"unexpected HEAD tree entry for {path!r}")
    old_mode, kind, old_oid = parts
    if kind != b"blob" or not _OBJECT_NAME.fullmatch(old_oid.decode("ascii", "replace")):
        return ChangeStatus.TYPE_CHANGED
    try:
        current_mode = (toplevel / path).lstat().st_mode
        if stat.S_ISLNK(current_mode):
            if old_mode != b"120000":
                return ChangeStatus.TYPE_CHANGED
            current = os.fsencode(os.readlink(toplevel / path))
            original = _git_or_raise(
                git_executable, toplevel, ("cat-file", "blob", old_oid.decode("ascii")),
                f"cannot inspect HEAD path {path!r}", LocalChangeError,
                "working-tree change analysis",
            )
            return None if current == original else ChangeStatus.MODIFIED
        if not stat.S_ISREG(current_mode) or old_mode not in (b"100644", b"100755"):
            return ChangeStatus.TYPE_CHANGED
        current_oid = _single_object_name(
            _git_or_raise(
                git_executable, toplevel, ("hash-object", "--no-filters", "--", path),
                f"cannot hash worktree path {path!r}", LocalChangeError,
                "working-tree change analysis",
            ),
            f"cannot hash worktree path {path!r}", LocalChangeError,
        )
    except OSError as exc:
        raise LocalChangeError(f"cannot inspect worktree path {path!r}: {exc}") from None
    executable = bool(current_mode & 0o111)
    if current_oid == old_oid.decode("ascii") and executable == (old_mode == b"100755"):
        return None
    return ChangeStatus.MODIFIED


def parse_untracked_paths(output: bytes) -> tuple[str, ...]:
    """Parse Git's NUL-delimited, repository-relative untracked path listing."""
    if output and not output.endswith(b"\0"):
        raise LocalChangeError("unterminated path in git ls-files output")
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if any(not raw for raw in fields):
        raise LocalChangeError("unexpected empty path in git ls-files output")
    return tuple(sorted({_checked_path(raw, LocalChangeError, "git ls-files") for raw in fields}))


def _local_context(root: Path, git_executable: str, source: str) -> tuple[Path, str, str | None]:
    option = f"{source} change analysis"
    toplevel_output = _git_or_raise(
        git_executable, root, ("rev-parse", "--show-toplevel"),
        f"{root} is not inside a Git worktree", LocalChangeError, option,
    )
    toplevel = Path(os.fsdecode(toplevel_output).rstrip("\n"))
    prefix_output = _git_or_raise(
        git_executable, root, ("rev-parse", "--show-prefix"),
        f"cannot locate {root} within Git worktree {toplevel}", LocalChangeError, option,
    )
    prefix = os.fsdecode(prefix_output).removesuffix("\n").removesuffix("/")
    try:
        resolved = _invoke(git_executable, root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    except subprocess.TimeoutExpired:
        raise LocalChangeError("cannot resolve HEAD: git timed out") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalChangeError(f"cannot resolve HEAD: {exc}") from None
    if resolved.returncode == 0:
        return toplevel, prefix, _single_object_name(
            resolved.stdout, "cannot resolve HEAD to a commit", LocalChangeError
        )
    # Only a symbolic branch with no ref is an initial, unborn HEAD. A broken
    # existing ref must remain an error rather than becoming an empty baseline.
    symbolic = _git_or_raise(
        git_executable, root, ("symbolic-ref", "-q", "HEAD"),
        "cannot resolve HEAD to a commit", LocalChangeError, option,
    ).decode("ascii", "replace").strip()
    if not symbolic.startswith("refs/heads/"):
        raise LocalChangeError("cannot resolve HEAD to a commit")
    try:
        present = _invoke(git_executable, root, "show-ref", "--verify", "--quiet", symbolic)
    except subprocess.TimeoutExpired:
        raise LocalChangeError("cannot verify HEAD: git timed out") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalChangeError(f"cannot verify HEAD: {exc}") from None
    if present.returncode != 1:
        raise LocalChangeError("cannot resolve HEAD to a commit")
    return toplevel, prefix, None


def parse_name_status(
    output: bytes, error: type[RepoCheckError] = ComparisonError
) -> tuple[tuple[str, str, str | None], ...]:
    """Parse ``git diff --name-status -z`` output into (status letter, path,
    old path) tuples of repository-relative paths. Unexpected output raises
    the supplied error type rather than producing uncertain facts."""
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    entries = []
    index = 0
    while index < len(fields):
        token = fields[index].decode("ascii", errors="replace")
        letter, score = token[:1], token[1:]
        if letter in _SINGLE_PATH_STATUS and score == "":
            paths = fields[index + 1 : index + 2]
            if len(paths) != 1:
                raise error(f"unexpected git diff output after status {token!r}")
            entries.append((letter, _checked_path(paths[0], error), None))
            index += 2
        elif letter in _TWO_PATH_STATUS and score.isdigit():
            paths = fields[index + 1 : index + 3]
            if len(paths) != 2:
                raise error(f"unexpected git diff output after status {token!r}")
            entries.append((letter, _checked_path(paths[1], error), _checked_path(paths[0], error)))
            index += 3
        else:
            raise error(f"unexpected git diff status {token!r}")
    return tuple(entries)


def scope_changes(
    entries: tuple[tuple[str, str, str | None], ...], prefix: str
) -> tuple[GitChange, ...]:
    """Keep changes beneath ``prefix`` (a repository-relative directory, or ""
    for the repository root) with paths relative to it.

    A rename or copy whose two sides straddle the analysis root keeps only its
    inside side: a rename into the root is ADDED, a rename out of it is
    DELETED, and a copy from outside is ADDED (a copy leaves its source
    unchanged, so a copy out of the root is not a change inside it).
    """

    def inside(path: str) -> str | None:
        if not prefix:
            return path
        return path[len(prefix) + 1 :] if path.startswith(prefix + "/") else None

    changes = []
    for letter, path, old_path in entries:
        new_inside = inside(path)
        if letter in _SINGLE_PATH_STATUS:
            if new_inside is not None:
                changes.append(GitChange(_SINGLE_PATH_STATUS[letter], new_inside))
            continue
        old_inside = inside(old_path) if old_path is not None else None
        if new_inside is not None and old_inside is not None:
            changes.append(GitChange(_TWO_PATH_STATUS[letter], new_inside, old_inside))
        elif new_inside is not None:
            changes.append(GitChange(ChangeStatus.ADDED, new_inside))
        elif old_inside is not None and letter == "R":
            changes.append(GitChange(ChangeStatus.DELETED, old_inside))
    return tuple(sorted(changes, key=lambda c: (c.path, c.old_path or "", c.status.value)))


# The only log arguments used for history. Everything that user or
# repository config could change is pinned: traversal order (--topo-order),
# merge commits excluded, root commits diffed against the
# empty tree, no rename or copy detection (a rename touches its old and new
# path), gitlink changes always reported, and paths relative to the worktree
# root. ``--raw`` output is used because each entry starts with ":", so it
# can never be confused with a commit line. Signatures, colors, external
# diff drivers, and text conversion are off.
_LOG_ARGS = (
    "log",
    "-z",
    "--raw",
    "--no-abbrev",
    "--format=%H",
    "--topo-order",
    "--no-merges",
    "--root",
    "--no-renames",
    "--no-relative",
    "--ignore-submodules=none",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-show-signature",
)

# Raw diff statuses possible without rename/copy detection.
_RAW_STATUSES = frozenset("ADMT")


def read_git_history(root: Path, max_commits: int, git_executable: str = "git") -> HistoryState:
    """At most ``max_commits`` non-merge commits reachable from the resolved
    HEAD, in Git's explicitly pinned ``--topo-order`` traversal, with the
    paths each touched scoped to the analysis directory ``root``. The window
    is repository-wide: commits that touched nothing beneath ``root`` are
    kept with no paths. ``head_commit`` records the resolved HEAD, which may
    be a merge. Raises ``HistoryError`` on any failure.

    Only local objects are read; nothing is fetched. A shallow repository is
    not an error: its locally visible history is reported, and
    ``is_shallow_repository`` records that older history may be missing.
    """
    if type(max_commits) is not int or max_commits < 1:
        raise HistoryError(f"history depth must be a positive integer, got {max_commits!r}")
    toplevel_output = _git_or_raise(
        git_executable, root, ("rev-parse", "--show-toplevel"),
        f"{root} is not inside a Git worktree", HistoryError, "--history",
    )
    toplevel = Path(os.fsdecode(toplevel_output).rstrip("\n"))
    prefix_output = _git_or_raise(
        git_executable, root, ("rev-parse", "--show-prefix"),
        f"cannot locate {root} within Git worktree {toplevel}", HistoryError, "--history",
    )
    prefix = os.fsdecode(prefix_output).removesuffix("\n").removesuffix("/")
    head_commit = _resolve_commit(
        git_executable, root, "HEAD", "cannot resolve HEAD to a commit", HistoryError, "--history"
    )
    shallow = _git_or_raise(
        git_executable, root, ("rev-parse", "--is-shallow-repository"),
        "cannot determine whether the repository is shallow", HistoryError, "--history",
    ).decode("ascii", errors="replace").strip()
    if shallow not in ("true", "false"):
        raise HistoryError(f"unexpected git rev-parse --is-shallow-repository output {shallow!r}")
    log = _git_or_raise(
        git_executable, toplevel, (*_LOG_ARGS, f"--max-count={max_commits}", head_commit, "--"),
        "git log failed", HistoryError, "--history",
    )
    commits = parse_history_log(log)
    if len(commits) > max_commits:
        raise HistoryError(f"git log returned more than {max_commits} commits")
    return HistoryState(
        requested_commits=max_commits,
        head_commit=head_commit,
        commits=scope_history(commits, prefix),
        is_shallow_repository=shallow == "true",
    )


def parse_history_log(output: bytes) -> tuple[HistoryCommit, ...]:
    """Parse ``git log -z --raw --format=%H`` output into commits in output
    order, each with sorted, unique repository-relative paths.

    The output is a NUL-separated sequence of fields: a commit's object name,
    then one pair of fields per changed path (the raw entry, beginning with
    ":", optionally preceded by a newline, and the path). Unexpected output
    raises ``HistoryError`` rather than producing uncertain facts.
    """
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    commits: list[HistoryCommit] = []
    commit: str | None = None
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        token = fields[index].removeprefix(b"\n")
        if token.startswith(b":"):
            status = token.rsplit(b" ", 1)[-1].decode("ascii", errors="replace")
            if commit is None or status not in _RAW_STATUSES or index + 1 >= len(fields):
                raise HistoryError(f"unexpected git log entry {token[:80]!r}")
            paths.add(_checked_path(fields[index + 1], HistoryError, "git log"))
            index += 2
            continue
        name = token.decode("ascii", errors="replace")
        if not _OBJECT_NAME.fullmatch(name):
            raise HistoryError(f"unexpected git log output {token[:80]!r}")
        if commit is not None:
            commits.append(HistoryCommit(commit, tuple(sorted(paths))))
        commit, paths = name, set()
        index += 1
    if commit is not None:
        commits.append(HistoryCommit(commit, tuple(sorted(paths))))
    return tuple(commits)


def scope_history(commits: tuple[HistoryCommit, ...], prefix: str) -> tuple[HistoryCommit, ...]:
    """Keep each commit's paths beneath ``prefix`` (a repository-relative
    directory, or "" for the repository root), relative to it. Commits are
    kept, in order, even when none of their paths remain."""
    if not prefix:
        return commits
    start = len(prefix) + 1
    return tuple(
        HistoryCommit(
            commit.commit,
            tuple(path[start:] for path in commit.touched_paths if path.startswith(prefix + "/")),
        )
        for commit in commits
    )


def _checked_path(
    raw: bytes, error: type[RepoCheckError] = ComparisonError, source: str = "git diff"
) -> str:
    path = os.fsdecode(raw)
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise error(f"unexpected path in {source} output: {path!r}")
    return path


def _resolve_commit(
    git_executable: str,
    root: Path,
    ref: str,
    failure: str,
    error: type[RepoCheckError] = ComparisonError,
    option: str = "--compare",
) -> str:
    output = _git_or_raise(
        git_executable, root, ("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"), failure,
        error, option,
    )
    return _single_object_name(output, failure, error)


def _single_object_name(
    output: bytes, failure: str, error: type[RepoCheckError] = ComparisonError
) -> str:
    text = output.decode("ascii", errors="replace").strip()
    if not _OBJECT_NAME.fullmatch(text):
        raise error(failure)
    return text


def _git_or_raise(
    git_executable: str,
    root: Path,
    args: tuple[str, ...],
    failure: str,
    error: type[RepoCheckError] = ComparisonError,
    option: str = "--compare",
) -> bytes:
    """Run Git for an explicit request (``option``): any failure raises ``error``."""
    try:
        completed = _invoke(git_executable, root, *args)
    except FileNotFoundError:
        raise error(
            f"{option} requires Git, but the {git_executable!r} executable was not found"
        ) from None
    except subprocess.TimeoutExpired:
        raise error(f"{failure}: git timed out") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise error(f"{failure}: {exc}") from None
    if completed.returncode != 0:
        detail = os.fsdecode(completed.stderr).strip().splitlines()
        raise error(f"{failure}: {detail[-1]}" if detail else failure)
    return completed.stdout


def _run_git(git_executable: str, root: Path, *args: str) -> bytes | None:
    """Run Git for optional facts: any failure yields None."""
    try:
        completed = _invoke(git_executable, root, *args)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _invoke(git_executable: str, root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """The single place a Git subprocess is started."""
    env = {k: v for k, v in os.environ.items() if k not in _REPOSITORY_ENV_VARS}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        [git_executable, *_GLOBAL_OPTIONS, "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=env,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
        shell=False,
    )
