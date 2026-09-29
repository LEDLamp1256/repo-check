"""PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE: source changed, no test changed."""

from __future__ import annotations

from collections.abc import Iterator

from repo_check.model import (
    FileClassification,
    Finding,
    RepositorySnapshot,
    RuleMetadata,
    Severity,
)
from repo_check.rules.base import RuleContext


class ProductionChangeWithoutTestChange:
    metadata = RuleMetadata(
        id="PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE",
        title="Production change without test change",
        category="change",
        default_severity=Severity.WARNING,
        description=(
            "Reports a selected change set that changes production source files but no test "
            "files. Additions, modifications, deletions, renames, and copies all count; an "
            "entry counts as production or test by the classification of any path it touches."
        ),
        remediation=(
            "Check whether the change should include new or updated tests."
        ),
    )

    def check_change(self, snapshot: RepositorySnapshot, context: RuleContext) -> Iterator[Finding]:
        change_state = snapshot.change
        if change_state is None:
            return
        production = test = 0
        for change in change_state.changes:
            kinds = change.classifications
            production += FileClassification.SOURCE in kinds
            test += FileClassification.TEST in kinds
        if production == 0 or test > 0:
            return
        yield Finding(
            rule_id=self.metadata.id,
            severity=self.metadata.default_severity,
            message="Production source files changed without any test file changes.",
            path=None,
            evidence={"production_files": production, "test_files": 0},
        )
