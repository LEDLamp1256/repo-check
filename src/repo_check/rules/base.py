"""Minimal internal rule interfaces.

Four rule kinds exist, run explicitly by the engine:

* ``FileRule``: called once per discovered file via ``check_file``.
* ``RepositoryRule``: called once per analysis via ``check_repository`` with
  the whole normalized ``RepositorySnapshot`` (including Git state).
* ``ChangeRule``: called once per analysis via ``check_change``, when change
  analysis was requested; ``snapshot.change`` is then never None.
* ``HistoryRule``: called once per analysis via ``check_history``, only when
  history was requested; ``snapshot.history`` is then never None.

Rules only read what the engine hands them. They never walk the repository,
run Git, load configuration, apply exclusions, classify paths, or build
snapshots. A rule that
needs file contents reads a discovered file through
``repo_check.discovery.read_text_lines`` (the single decoding policy), and
decides path existence with ``RepositorySnapshot.path_kind``. Rules that need
parsed source use ``RuleContext.facts`` and never parse it themselves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from repo_check.config import RuleConfig
from repo_check.languages.facts import AnalysisFacts
from repo_check.model import DiscoveredFile, Finding, RepositorySnapshot, RuleMetadata


@dataclass(frozen=True)
class RuleContext:
    """Per-rule context supplied by the engine.

    ``facts`` is shared by all rules in one analysis run, so language facts
    (parsed Python, scanned Swift) are computed at most once per file.
    """

    settings: RuleConfig
    facts: AnalysisFacts = field(default_factory=AnalysisFacts)


class FileRule(Protocol):
    metadata: RuleMetadata

    def check_file(self, file: DiscoveredFile, context: RuleContext) -> Iterable[Finding]: ...


class RepositoryRule(Protocol):
    metadata: RuleMetadata

    def check_repository(
        self, snapshot: RepositorySnapshot, context: RuleContext
    ) -> Iterable[Finding]: ...


class ChangeRule(Protocol):
    metadata: RuleMetadata

    def check_change(
        self, snapshot: RepositorySnapshot, context: RuleContext
    ) -> Iterable[Finding]: ...


class HistoryRule(Protocol):
    metadata: RuleMetadata

    def check_history(
        self, snapshot: RepositorySnapshot, context: RuleContext
    ) -> Iterable[Finding]: ...
