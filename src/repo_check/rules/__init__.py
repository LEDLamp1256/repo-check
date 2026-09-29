"""Built-in rule registry."""

from __future__ import annotations

from repo_check.rules.base import (
    ChangeRule,
    FileRule,
    HistoryRule,
    RepositoryRule,
    RuleContext,
)
from repo_check.rules.change.large_changeset import LargeChangeset
from repo_check.rules.change.production_change import ProductionChangeWithoutTestChange
from repo_check.rules.change.sensitive_project_file import SensitiveProjectFileChanged
from repo_check.rules.common.broken_local_doc_link import BrokenLocalDocLink
from repo_check.rules.common.file_too_large import FileTooLarge
from repo_check.rules.common.todo_comment import TodoComment
from repo_check.rules.common.tracked_build_artifact import TrackedBuildArtifact
from repo_check.rules.history.frequently_changed_file import FrequentlyChangedFile
from repo_check.rules.python.bare_except import PythonBareExcept
from repo_check.rules.python.definition_too_large import (
    PythonClassTooLarge,
    PythonFunctionTooLarge,
)
from repo_check.rules.python.mutable_default import PythonMutableDefault
from repo_check.rules.swift.forced_operators import SwiftForceCast, SwiftForceTry

# Finding order does not depend on registry order; the engine sorts findings.
FILE_RULES: tuple[FileRule, ...] = (
    FileTooLarge(),
    TodoComment(),
    PythonBareExcept(),
    PythonClassTooLarge(),
    PythonFunctionTooLarge(),
    PythonMutableDefault(),
    SwiftForceCast(),
    SwiftForceTry(),
)
REPOSITORY_RULES: tuple[RepositoryRule, ...] = (BrokenLocalDocLink(), TrackedBuildArtifact())
# Run when a change source was requested.
CHANGE_RULES: tuple[ChangeRule, ...] = (
    LargeChangeset(),
    ProductionChangeWithoutTestChange(),
    SensitiveProjectFileChanged(),
)
# Run only when history is requested (--history).
HISTORY_RULES: tuple[HistoryRule, ...] = (FrequentlyChangedFile(),)

ALL_RULES: tuple[FileRule | RepositoryRule | ChangeRule | HistoryRule, ...] = tuple(
    sorted(
        (*FILE_RULES, *REPOSITORY_RULES, *CHANGE_RULES, *HISTORY_RULES),
        key=lambda rule: rule.metadata.id,
    )
)

__all__ = [
    "ALL_RULES",
    "CHANGE_RULES",
    "ChangeRule",
    "FILE_RULES",
    "HISTORY_RULES",
    "REPOSITORY_RULES",
    "FileRule",
    "HistoryRule",
    "RepositoryRule",
    "RuleContext",
]
