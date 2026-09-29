"""Repository discovery and centralized file classification.

Discovery walks the repository once and produces a ``RepositorySnapshot``.
Rules consume that snapshot and never walk the filesystem themselves.

Policy:

* VCS metadata (``.git`` etc., directory or file) is skipped, and directories
  named in ``PRUNED_DIRECTORIES`` (dependency and tool caches) are never
  entered.
* Symlinks are never followed or reported, whether they point to files or
  directories, inside or outside the repository.
* Only regular files are reported (no FIFOs, sockets, or devices).
* Directories entered are recorded, and entries seen but not indexed
  (pruned or excluded paths, symlinks, special files) are recorded as
  unindexed, so rules can tell "missing" from "not indexed".
* Configured exclusions are applied before any file content is read.
* Files are ordered by their POSIX relative path (plain string order).
* Git state, when supplied, is attached to the snapshot with configured
  exclusions applied. Its tracked paths are not limited to files the walk
  visited, so tracked files inside pruned directories remain visible.

Exclusion patterns are matched with ``fnmatch`` against the full POSIX path
relative to the repository root. ``*`` also matches ``/``, so ``*.min.js``
matches at any depth. A pattern that matches a directory excludes everything
beneath it. A pattern ending in ``/`` matches directories only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from repo_check.config import Config
from repo_check.errors import DiscoveryError
from repo_check.model import (
    ChangedPath,
    ChangeState,
    ChangeSource,
    DiscoveredFile,
    FileClassification,
    GitComparison,
    GitLocalChanges,
    GitState,
    HistoryCommit,
    HistoryState,
    RepositorySnapshot,
)

BINARY_SNIFF_BYTES = 8192

# VCS metadata. Skipped whether it is a directory or a file (a ``.git`` file
# points at the real git directory in worktrees and submodules).
VCS_METADATA = frozenset({".git", ".hg", ".svn"})

# Never entered. Matched against the exact directory name.
PRUNED_DIRECTORIES = VCS_METADATA | frozenset(
    {
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".venv",
        "node_modules",
        ".build",
    }
)

# Files beneath these are classified BUILD_ARTIFACT. Directory names are
# compared case-insensitively. Generic names (``build``, ``dist``) count only
# as the first directory component relative to the analyzed path: nested
# directories with these names are often ordinary source or documentation
# (a ``dist`` package, docs about a "build" feature). Distinctive names count
# at any depth. ``.build`` (SwiftPM output) is also in PRUNED_DIRECTORIES, so
# the walk never enters it; this entry classifies paths known from
# elsewhere, such as Git-tracked paths.
TOP_LEVEL_BUILD_DIRECTORIES = frozenset({"build", "dist"})
BUILD_DIRECTORIES = frozenset({".build"})
BUILD_DIRECTORY_SUFFIXES = (".egg-info",)

BUILD_ARTIFACT_EXTENSIONS = frozenset(
    {
        ".pyc", ".pyo", ".class", ".o", ".obj", ".so", ".dylib", ".dll",
        ".a", ".lib", ".exe",
    }
)

BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".icns", ".webp", ".tiff",
        ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".tar", ".rar",
        ".mp3", ".mp4", ".mov", ".wav", ".ogg", ".woff", ".woff2", ".ttf", ".otf",
        ".sqlite", ".db",
        # Archives that are routinely committed on purpose (e.g. Gradle's
        # gradle-wrapper.jar, vendored wheels): binary, not build artifacts.
        ".jar", ".whl",
    }
) | BUILD_ARTIFACT_EXTENSIONS

GENERATED_FILENAMES = frozenset(
    {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
        "Pipfile.lock", "uv.lock", "Cargo.lock", "Gemfile.lock", "composer.lock",
        "Package.resolved", "go.sum",
    }
)
GENERATED_SUFFIXES = (".min.js", ".min.css", "_pb2.py", "_pb2_grpc.py", ".pb.go", ".js.map")
GENERATED_HEADER_MARKERS = (b"@generated", b"DO NOT EDIT")
GENERATED_HEADER_LINES = 5

SOURCE_EXTENSIONS = frozenset(
    {
        ".py", ".pyi", ".swift", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh",
        ".m", ".mm", ".java", ".kt", ".kts", ".scala", ".go", ".rs", ".rb", ".php",
        ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".cs", ".sh", ".bash", ".zsh",
        ".lua", ".pl", ".dart", ".ex", ".exs", ".erl", ".hs", ".ml", ".clj",
    }
)

TEST_DIRECTORIES = frozenset({"test", "tests", "__tests__", "spec", "specs"})

DOCUMENTATION_EXTENSIONS = frozenset({".md", ".markdown", ".rst", ".adoc"})
DOCUMENTATION_STEMS = frozenset(
    {"readme", "license", "licence", "changelog", "contributing", "authors", "notice", "copying"}
)

CONFIGURATION_EXTENSIONS = frozenset(
    {".toml", ".yaml", ".yml", ".ini", ".cfg", ".json", ".conf", ".properties", ".plist"}
)
CONFIGURATION_FILENAMES = frozenset(
    {
        ".gitignore", ".gitattributes", ".gitmodules", ".editorconfig", ".dockerignore",
        "requirements.txt", "requirements-dev.txt",
    }
)


def discover(
    root: Path,
    config: Config,
    git_state: GitState | None = None,
    comparison: GitComparison | None = None,
    history: HistoryState | None = None,
    local_changes: GitLocalChanges | None = None,
) -> RepositorySnapshot:
    """Walk ``root`` and return a normalized, deterministic snapshot.

    ``git_state`` (from ``repo_check.git``) is attached after configured
    exclusions are applied to its tracked paths. ``comparison``, when given,
    is normalized as comparison change analysis in ``RepositorySnapshot.change``
    (see ``normalize_changes``). ``history`` is normalized into
    ``RepositorySnapshot.history`` (see ``normalize_history``). ``local_changes``
    can instead supply a requested local source for ``RepositorySnapshot.change``.
    """
    if comparison is not None and local_changes is not None:
        raise ValueError("only one change source can be normalized at a time")
    resolved_root = resolve_root(root)

    walk = _WalkResult()
    _walk(resolved_root, PurePosixPath(), config.exclude, walk)
    files = sorted(walk.files, key=lambda f: f.relative_path)

    git = git_state or GitState.unavailable()
    if git.available and config.exclude:
        git = GitState(
            available=True,
            repository_root=git.repository_root,
            tracked_paths=tuple(
                path for path in git.tracked_paths if not is_path_excluded(path, config.exclude)
            ),
        )
    return RepositorySnapshot(
        root=resolved_root,
        files=tuple(files),
        config=config,
        git=git,
        directories=tuple(sorted(walk.directories)),
        unindexed_paths=tuple(sorted(walk.unindexed)),
        change=(
            normalize_changes(comparison or local_changes, files, config.exclude)
            if comparison is not None or local_changes is not None
            else None
        ),
        history=normalize_history(history, config.exclude) if history is not None else None,
    )


def normalize_history(history: HistoryState, exclude: tuple[str, ...]) -> HistoryState:
    """Apply configured exclusions to historical paths.

    Excluded paths are removed from each commit. Commits are kept even when
    no path remains, so the analyzed window stays the requested one.
    """
    if not exclude:
        return history
    return HistoryState(
        requested_commits=history.requested_commits,
        head_commit=history.head_commit,
        commits=tuple(
            HistoryCommit(
                commit=commit.commit,
                touched_paths=tuple(
                    path for path in commit.touched_paths if not is_path_excluded(path, exclude)
                ),
            )
            for commit in history.commits
        ),
        is_shallow_repository=history.is_shallow_repository,
    )


def normalize_changes(
    raw: GitComparison | GitLocalChanges,
    files: list[DiscoveredFile],
    exclude: tuple[str, ...],
) -> ChangeState:
    """Attach centralized classification to changes from a requested source.

    A path that is a discovered file keeps the snapshot's classification
    (which may use content, e.g. generated-file headers). Any other path
    (deleted, renamed away, excluded, in a pruned directory, or a symlink) is
    classified from its path alone with ``classify_path``. An entry is dropped
    when every path it touches is excluded by ``repo_check.exclude``.
    """
    known = {file.relative_path: file.classification for file in files}

    def classification(path: str) -> FileClassification:
        return known[path] if path in known else classify_path(path)

    changes = []
    for change in raw.changes:
        touched = (change.path,) if change.old_path is None else (change.path, change.old_path)
        if all(is_path_excluded(path, exclude) for path in touched):
            continue
        changes.append(
            ChangedPath(
                status=change.status,
                path=change.path,
                classification=classification(change.path),
                old_path=change.old_path,
                old_classification=(
                    classification(change.old_path) if change.old_path is not None else None
                ),
            )
        )
    normalized = tuple(sorted(changes, key=lambda c: (c.path, c.old_path or "", c.status.value)))
    if isinstance(raw, GitComparison):
        return ChangeState(
            source=ChangeSource.COMPARISON,
            changes=normalized,
            requested_ref=raw.requested_ref,
            compare_commit=raw.compare_commit,
            merge_base=raw.merge_base,
            head_commit=raw.head_commit,
        )
    return ChangeState(source=raw.source, changes=normalized)


def classify_path(relative_path: str) -> FileClassification:
    """Classify a path whose content is not available (for example, deleted).

    Uses ``classify`` with binary status inferred from the extension only, so
    content-based signals (NUL bytes, generated-file headers) are not applied.
    """
    path = PurePosixPath(relative_path)
    return classify(path, is_binary=path.suffix.lower() in BINARY_EXTENSIONS)


def resolve_root(root: Path) -> Path:
    """Validate the analysis path and return it resolved."""
    if not root.exists():
        raise DiscoveryError(f"path does not exist: {root}")
    if not root.is_dir():
        raise DiscoveryError(f"path is not a directory: {root}")
    return root.resolve()


@dataclass
class _WalkResult:
    files: list[DiscoveredFile] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    unindexed: list[str] = field(default_factory=list)


def _walk(
    directory: Path,
    relative: PurePosixPath,
    exclude: tuple[str, ...],
    out: _WalkResult,
) -> None:
    try:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda e: e.name)
    except OSError as exc:
        raise DiscoveryError(f"cannot read directory {directory}: {exc.strerror}") from exc

    for entry in entries:
        rel = relative / entry.name
        rel_str = rel.as_posix()
        if entry.is_symlink() or entry.name in VCS_METADATA:
            out.unindexed.append(rel_str)
        elif entry.is_dir(follow_symlinks=False):
            if entry.name in PRUNED_DIRECTORIES or is_excluded(rel_str, exclude, is_dir=True):
                out.unindexed.append(rel_str)
                continue
            out.directories.append(rel_str)
            _walk(Path(entry.path), rel, exclude, out)
        elif entry.is_file(follow_symlinks=False):
            if is_excluded(rel_str, exclude, is_dir=False):
                out.unindexed.append(rel_str)
                continue
            out.files.append(_inspect_file(Path(entry.path), rel))
        else:
            out.unindexed.append(rel_str)  # FIFO, socket, device


def is_excluded(relative_path: str, patterns: tuple[str, ...], *, is_dir: bool) -> bool:
    for pattern in patterns:
        if pattern.endswith("/"):
            if is_dir and fnmatchcase(relative_path, pattern.rstrip("/")):
                return True
        elif fnmatchcase(relative_path, pattern):
            return True
    return False


def is_path_excluded(relative_path: str, patterns: tuple[str, ...]) -> bool:
    """Apply exclusions to a path that was not reached by walking.

    Equivalent to the walk: excluded if any ancestor directory matches as a
    directory, or the path itself matches as a file.
    """
    parts = relative_path.split("/")
    for depth in range(1, len(parts)):
        if is_excluded("/".join(parts[:depth]), patterns, is_dir=True):
            return True
    return is_excluded(relative_path, patterns, is_dir=False)


def read_text_lines(file: DiscoveredFile) -> tuple[str, ...] | None:
    """Return the decoded lines of a discovered text file, or None if binary.

    The single content-decoding policy for rules: UTF-8 with invalid bytes
    replaced (deterministic), a leading BOM removed, and lines split on
    ``\n`` with a trailing ``\r`` stripped, so line numbers match
    ``DiscoveredFile.line_count``.
    """
    if file.is_binary:
        return None
    try:
        data = file.absolute_path.read_bytes()
    except OSError as exc:
        raise DiscoveryError(f"cannot read file {file.relative_path}: {exc.strerror}") from exc
    text = data.decode("utf-8", errors="replace").removeprefix("\ufeff")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return tuple(line.removesuffix("\r") for line in lines)


def _inspect_file(path: Path, relative: PurePosixPath) -> DiscoveredFile:
    try:
        size = path.stat().st_size
        is_binary, line_count, head = _read_content(path, relative.suffix.lower())
    except OSError as exc:
        raise DiscoveryError(
            f"cannot read file {relative.as_posix()}: {exc.strerror} "
            "(add it to repo_check.exclude to skip it)"
        ) from exc
    return DiscoveredFile(
        relative_path=relative.as_posix(),
        absolute_path=path,
        size_bytes=size,
        is_binary=is_binary,
        line_count=line_count,
        classification=classify(relative, is_binary=is_binary, head=head),
    )


def _read_content(path: Path, suffix: str) -> tuple[bool, int | None, bytes]:
    """Return (is_binary, line_count, first chunk) for a file.

    Lines are counted as newline-terminated lines, plus one for trailing
    content without a final newline. An empty file has zero lines.
    """
    if suffix in BINARY_EXTENSIONS:
        return True, None, b""
    with path.open("rb") as handle:
        head = handle.read(BINARY_SNIFF_BYTES)
        if b"\0" in head:
            return True, None, head
        newlines = head.count(b"\n")
        last = head[-1:]
        while chunk := handle.read(1 << 16):
            newlines += chunk.count(b"\n")
            last = chunk[-1:]
    line_count = newlines + (1 if last and last != b"\n" else 0)
    return False, line_count, head


def classify(relative: PurePosixPath, *, is_binary: bool, head: bytes = b"") -> FileClassification:
    """Classify a file conservatively from its path and leading content.

    Precedence: build artifact, generated, binary, test, source,
    documentation, configuration, unknown.
    """
    name = relative.name
    suffix = relative.suffix.lower()
    dir_parts = [part.lower() for part in relative.parts[:-1]]

    if is_build_artifact_path(relative):
        return FileClassification.BUILD_ARTIFACT

    if name in GENERATED_FILENAMES or name.endswith(GENERATED_SUFFIXES):
        return FileClassification.GENERATED
    if not is_binary and _has_generated_header(head):
        return FileClassification.GENERATED

    if is_binary:
        return FileClassification.BINARY

    if suffix in SOURCE_EXTENSIONS:
        if _is_test_path(relative, dir_parts):
            return FileClassification.TEST
        return FileClassification.SOURCE

    stem = relative.stem.lower() if suffix else name.lower()
    if suffix in DOCUMENTATION_EXTENSIONS or stem in DOCUMENTATION_STEMS:
        return FileClassification.DOCUMENTATION

    if suffix in CONFIGURATION_EXTENSIONS or name in CONFIGURATION_FILENAMES:
        return FileClassification.CONFIGURATION

    return FileClassification.UNKNOWN


def is_build_artifact_path(relative: PurePosixPath | str) -> bool:
    """Path-only build-artifact test; the first step of ``classify``.

    Usable for paths with no ``DiscoveredFile`` (e.g. Git-tracked paths).
    A path is a build artifact exactly when ``classify`` would return
    BUILD_ARTIFACT for it.
    """
    relative = PurePosixPath(relative)
    if relative.suffix.lower() in BUILD_ARTIFACT_EXTENSIONS:
        return True
    dir_parts = [part.lower() for part in relative.parts[:-1]]
    if dir_parts and dir_parts[0] in TOP_LEVEL_BUILD_DIRECTORIES:
        return True
    return any(
        part in BUILD_DIRECTORIES or part.endswith(BUILD_DIRECTORY_SUFFIXES)
        for part in dir_parts
    )


def _has_generated_header(head: bytes) -> bool:
    lines = head.split(b"\n", GENERATED_HEADER_LINES)[:GENERATED_HEADER_LINES]
    return any(marker in line for line in lines for marker in GENERATED_HEADER_MARKERS)


def _is_test_path(relative: PurePosixPath, dir_parts: list[str]) -> bool:
    if any(part in TEST_DIRECTORIES for part in dir_parts):
        return True
    stem = relative.name.split(".", 1)[0]
    if stem.startswith("test_") or stem.endswith("_test"):
        return True
    # CamelCase conventions (Swift, Java, Kotlin): FooTests.swift, FooTest.java
    if stem.endswith(("Test", "Tests")) and stem not in ("Test", "Tests"):
        return True
    # JavaScript/TypeScript conventions: foo.test.ts, foo.spec.js
    return ".test." in relative.name or ".spec." in relative.name
