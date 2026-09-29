"""Error types for execution and configuration failures.

Any ``RepoCheckError`` that escapes the engine is reported by the CLI as an
execution/configuration error (exit status 2). Findings are never errors.
"""

from __future__ import annotations


class RepoCheckError(Exception):
    """Base class for failures that prevent analysis from completing."""


class ConfigError(RepoCheckError):
    """Configuration is missing, unreadable, malformed, or invalid."""


class DiscoveryError(RepoCheckError):
    """The repository could not be discovered (bad path, unreadable file)."""


class ComparisonError(RepoCheckError):
    """An explicit ``--compare`` request could not be carried out."""


class HistoryError(RepoCheckError):
    """An explicit ``--history`` request could not be carried out."""


class LocalChangeError(RepoCheckError):
    """A requested local change source could not be read from Git."""
