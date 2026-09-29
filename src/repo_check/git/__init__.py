"""Internal Git subsystem: produces normalized Git facts for the engine.

Rules never import this package; they read ``RepositorySnapshot.git``,
``RepositorySnapshot.change``, and ``RepositorySnapshot.history``.
"""

from repo_check.git.client import (
    read_git_comparison,
    read_git_history,
    read_git_staged_changes,
    read_git_state,
    read_git_working_tree_changes,
)
from repo_check.model import GitComparison, GitLocalChanges, GitState, HistoryState

__all__ = [
    "GitComparison",
    "GitLocalChanges",
    "GitState",
    "HistoryState",
    "read_git_comparison",
    "read_git_history",
    "read_git_staged_changes",
    "read_git_state",
    "read_git_working_tree_changes",
]
