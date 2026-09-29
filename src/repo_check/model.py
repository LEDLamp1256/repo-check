"""Core data model shared by discovery, rules, the engine, and output.

Everything here is a plain frozen dataclass or enum so that analysis results
are simple, comparable, and deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from repo_check.config import Config

# Evidence is JSON-compatible data: null, bool, int, finite float, str,
# sequences of evidence values, and str-keyed mappings of evidence values.
EvidenceValue = Union[
    None, bool, int, float, str, Sequence["EvidenceValue"], Mapping[str, "EvidenceValue"]
]


class Severity(str, Enum):
    """Finding severity. Ordered: info < warning < error."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    def meets(self, threshold: Severity) -> bool:
        """Return True if this severity is at or above ``threshold``."""
        return self.rank >= threshold.rank


_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}


class FileClassification(str, Enum):
    """Centralized classification assigned to each file during discovery."""

    SOURCE = "source"
    TEST = "test"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    GENERATED = "generated"
    BUILD_ARTIFACT = "build_artifact"
    BINARY = "binary"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuleMetadata:
    """Static description of a rule, shared by all findings it produces."""

    id: str
    title: str
    category: str
    default_severity: Severity
    description: str
    remediation: str


@dataclass(frozen=True)
class Location:
    """A position inside a file. Lines are 1-based and inclusive."""

    start_line: int
    end_line: int | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        if self.start_line < 1:
            raise ValueError("start_line must be >= 1")
        if self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")


REPOSITORY_PATH_LABEL = "<repository>"


@dataclass(frozen=True)
class Finding:
    """A single result produced by a rule.

    ``path`` is the POSIX-style path relative to the repository root, or None
    for a finding about the repository as a whole (or another non-file
    scope). A finding without a path cannot have a location.

    ``evidence`` holds structured, deterministic facts supporting the finding.
    It is validated and stored as a canonical copy: mappings become dicts
    with sorted keys, sequences become lists in their original order.
    """

    rule_id: str
    severity: Severity
    message: str
    path: str | None
    location: Location | None = None
    evidence: Mapping[str, EvidenceValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path is None and self.location is not None:
            raise ValueError("a finding without a path cannot have a location")
        if not isinstance(self.evidence, Mapping):
            raise TypeError("evidence must be a mapping with string keys")
        object.__setattr__(self, "evidence", canonical_evidence(self.evidence))

    def sort_key(self) -> tuple[int, str, int, str, str]:
        """Stable ordering: repository findings first, then path, start line
        (0 when absent), rule id, message."""
        start_line = self.location.start_line if self.location else 0
        if self.path is None:
            return (0, "", start_line, self.rule_id, self.message)
        return (1, self.path, start_line, self.rule_id, self.message)


def canonical_evidence(value: Any, _where: str = "evidence") -> Any:
    """Validate evidence and return a canonical JSON-ready copy.

    Mappings must have ``str`` keys and are returned as dicts with sorted
    keys; sequences (other than strings/bytes) are returned as lists in their
    original order. Floats must be finite. Anything else (sets, bytes,
    arbitrary objects, enum members) raises TypeError or ValueError rather
    than rendering unpredictably.
    """
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{_where}: float must be finite, got {value!r}")
        return value
    if isinstance(value, Mapping):
        for key in value:
            if type(key) is not str:
                raise TypeError(f"{_where}: mapping keys must be str, got {key!r}")
        return {key: canonical_evidence(value[key], f"{_where}.{key}") for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [canonical_evidence(item, f"{_where}[{i}]") for i, item in enumerate(value)]
    raise TypeError(f"{_where}: unsupported evidence value of type {type(value).__name__}")


def sort_findings(findings: list[Finding] | tuple[Finding, ...]) -> tuple[Finding, ...]:
    return tuple(sorted(findings, key=Finding.sort_key))


@dataclass(frozen=True)
class DiscoveredFile:
    """Normalized metadata for one file found during discovery.

    ``line_count`` is ``None`` for binary files. ``is_binary`` is True when
    the file has a known binary extension or contains a NUL byte in its
    first 8 KiB.
    """

    relative_path: str
    absolute_path: Path
    size_bytes: int
    is_binary: bool
    line_count: int | None
    classification: FileClassification


@dataclass(frozen=True)
class GitState:
    """Normalized local Git facts for the analysis root.

    ``available`` is False when the analysis root is not inside a Git
    worktree, or Git cannot be run; the other fields are then empty.
    ``repository_root`` is the containing worktree root, which may be an
    ancestor of the analysis root. ``tracked_paths`` holds sorted POSIX paths
    relative to the analysis root, limited to paths beneath it.
    """

    available: bool
    repository_root: Path | None = None
    tracked_paths: tuple[str, ...] = ()

    @classmethod
    def unavailable(cls) -> GitState:
        return cls(available=False)


class ChangeStatus(str, Enum):
    """How a path changed within a requested change source."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"


@dataclass(frozen=True)
class GitChange:
    """One Git change entry, scoped to the analysis root.

    ``path`` is the current path (the deleted path for DELETED). ``old_path``
    is set only for RENAMED and COPIED. Paths are POSIX, relative to the
    analysis root. Produced by the Git layer; rules see ``ChangedPath``.
    """

    status: ChangeStatus
    path: str
    old_path: str | None = None


@dataclass(frozen=True)
class GitComparison:
    """Committed changes from ``merge-base(REF, HEAD)`` to ``HEAD``."""

    requested_ref: str
    compare_commit: str
    merge_base: str
    head_commit: str
    changes: tuple[GitChange, ...] = ()


class ChangeSource(str, Enum):
    """The source of normalized change analysis."""

    COMPARISON = "comparison"
    WORKING_TREE = "working_tree"
    STAGED = "staged"


@dataclass(frozen=True)
class GitLocalChanges:
    """Git changes from one local source, scoped to the analysis root."""

    source: ChangeSource
    changes: tuple[GitChange, ...] = ()

    def __post_init__(self) -> None:
        if self.source is not ChangeSource.STAGED and self.source is not ChangeSource.WORKING_TREE:
            raise ValueError("GitLocalChanges requires a staged or working-tree source")


@dataclass(frozen=True)
class ChangedPath:
    """A normalized changed entry with centralized classification.

    ``classification`` is for ``path``; ``old_classification`` for
    ``old_path`` (renames and copies only).
    """

    status: ChangeStatus
    path: str
    classification: FileClassification
    old_path: str | None = None
    old_classification: FileClassification | None = None

    @property
    def classifications(self) -> tuple[FileClassification, ...]:
        """Classifications of every path this entry touches."""
        if self.old_classification is None:
            return (self.classification,)
        return (self.classification, self.old_classification)


@dataclass(frozen=True)
class ChangeState:
    """Normalized change analysis for change rules.

    ``changes`` is sorted by ``path`` then ``old_path`` and already scoped to
    the analysis root with configured exclusions applied. Comparison provenance
    is populated for ``COMPARISON``; other sources need not have a ref or
    commits.
    """

    source: ChangeSource
    changes: tuple[ChangedPath, ...] = ()
    requested_ref: str | None = None
    compare_commit: str | None = None
    merge_base: str | None = None
    head_commit: str | None = None


@dataclass(frozen=True)
class HistoryCommit:
    """One non-merge commit of the analyzed history.

    ``commit`` is the full object name. ``touched_paths`` holds the sorted,
    unique POSIX paths (relative to the analysis root) that the commit added,
    modified, deleted, or changed the type of, compared with its parent (or,
    for a root commit, with the empty tree). A rename is two touched paths:
    the old and the new one. Paths outside the analysis root are omitted, so
    a commit may touch no paths.
    """

    commit: str
    touched_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class HistoryState:
    """Bounded local history of HEAD, when requested (``--history N``).

    ``head_commit`` is the full object name HEAD resolved to, the anchor of
    the history query. It may be a merge commit, which is not itself listed.
    ``commits`` are at most ``requested_commits`` non-merge commits reachable
    from ``head_commit``, in the order of Git's explicitly pinned
    ``--topo-order`` traversal. The window covers the whole repository: a
    commit that touched nothing beneath the analysis root is still listed,
    with no paths. ``is_shallow_repository`` is True when the repository is
    a shallow clone, so older history may exist that is not available
    locally.
    """

    requested_commits: int
    head_commit: str
    commits: tuple[HistoryCommit, ...] = ()
    is_shallow_repository: bool = False


class PathKind(str, Enum):
    """What the snapshot knows about a path relative to the analysis root."""

    FILE = "file"
    DIRECTORY = "directory"
    # Exists (or lies under something that exists) but discovery did not
    # index it: pruned or excluded paths, symlinks, special files. Rules
    # must treat these as unknown, not as missing.
    UNINDEXED = "unindexed"
    MISSING = "missing"


@dataclass(frozen=True)
class RepositorySnapshot:
    """Normalized repository state consumed by rules.

    ``files`` is sorted by ``relative_path`` and already excludes ignored
    directories, configured exclusions, and symlinks.

    ``git`` is an independent source of facts: its tracked paths are not
    limited to files that filesystem discovery visited (discovery prunes
    some cache/build directories), but configured exclusions do apply.

    ``directories`` lists the directories discovery entered (sorted,
    excluding the root). ``unindexed_paths`` lists entries discovery saw but
    did not index (sorted); see ``PathKind.UNINDEXED``.

    ``change`` holds normalized changes when a change source was requested,
    and is None otherwise. ``history`` holds bounded commit history only when
    it was requested, with configured exclusions applied to its paths.
    """

    root: Path
    files: tuple[DiscoveredFile, ...]
    config: Config
    git: GitState = field(default_factory=GitState.unavailable)
    directories: tuple[str, ...] = ()
    unindexed_paths: tuple[str, ...] = ()
    # Present when change analysis was requested.
    change: ChangeState | None = None
    # Present only when history was requested (``--history``).
    history: HistoryState | None = None

    def path_kind(self, relative_path: str) -> PathKind:
        """Classify a normalized relative POSIX path using snapshot facts only.

        The empty string is the analysis root. No filesystem access occurs.
        """
        if relative_path == "":
            return PathKind.DIRECTORY
        if relative_path in self._file_set:
            return PathKind.FILE
        if relative_path in self._directory_set:
            return PathKind.DIRECTORY
        parts = relative_path.split("/")
        for depth in range(1, len(parts) + 1):
            if "/".join(parts[:depth]) in self._unindexed_set:
                return PathKind.UNINDEXED
        return PathKind.MISSING

    @cached_property
    def _file_set(self) -> frozenset[str]:
        return frozenset(f.relative_path for f in self.files)

    @cached_property
    def _directory_set(self) -> frozenset[str]:
        return frozenset(self.directories)

    @cached_property
    def _unindexed_set(self) -> frozenset[str]:
        return frozenset(self.unindexed_paths)
