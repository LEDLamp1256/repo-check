"""LARGE_CHANGESET: more changed entries than the configured maximum."""

from __future__ import annotations

from collections.abc import Iterator

from repo_check.model import Finding, RepositorySnapshot, RuleMetadata, Severity
from repo_check.rules.base import RuleContext


class LargeChangeset:
    metadata = RuleMetadata(
        id="LARGE_CHANGESET",
        title="Large changeset",
        category="change",
        default_severity=Severity.WARNING,
        description=(
            "Reports a selected change set that touches more files than the "
            "configured maximum. A rename or copy counts as one changed file."
        ),
        remediation=(
            "Consider splitting the change into smaller, independently reviewable parts, or "
            "raise max_files if large changesets are expected."
        ),
    )

    def check_change(self, snapshot: RepositorySnapshot, context: RuleContext) -> Iterator[Finding]:
        change_state = snapshot.change
        if change_state is None:
            return
        # Validated as a positive int by config.
        maximum = int(context.settings.options["max_files"])
        actual = len(change_state.changes)
        if actual <= maximum:
            return
        yield Finding(
            rule_id=self.metadata.id,
            severity=self.metadata.default_severity,
            message=f"Changeset contains {actual} files; configured maximum is {maximum}.",
            path=None,
            evidence={"changed_files": actual, "maximum_files": maximum},
        )
