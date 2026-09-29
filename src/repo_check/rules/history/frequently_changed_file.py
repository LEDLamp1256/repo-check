"""FREQUENTLY_CHANGED_FILE: current source files touched by many scanned commits."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

from repo_check.model import (
    FileClassification,
    Finding,
    RepositorySnapshot,
    RuleMetadata,
    Severity,
)
from repo_check.rules.base import RuleContext


class FrequentlyChangedFile:
    metadata = RuleMetadata(
        id="FREQUENTLY_CHANGED_FILE",
        title="Frequently changed source file",
        category="maintainability",
        default_severity=Severity.INFO,
        description=(
            "Reports a current source file that was touched by at least "
            "min_touching_commits of the scanned non-merge commits and by at least "
            "min_touch_percent percent of them. Nothing is reported when fewer than "
            "min_scanned_commits commits were scanned."
        ),
        remediation=(
            "This is a factual signal about where changes concentrate in the scanned "
            "history. Review whether the file's responsibilities are expected to change "
            "this often; exclude the path or tune the thresholds if they are."
        ),
    )

    def check_history(
        self, snapshot: RepositorySnapshot, context: RuleContext
    ) -> Iterator[Finding]:
        history = snapshot.history
        if history is None:
            return
        # All three are validated as positive ints by config (percent <= 100).
        min_touching = int(context.settings.options["min_touching_commits"])
        min_percent = int(context.settings.options["min_touch_percent"])
        min_scanned = int(context.settings.options["min_scanned_commits"])

        scanned = len(history.commits)
        if scanned < min_scanned:
            return

        # Candidates are current SOURCE files only; historical paths that are
        # not current source files never produce findings.
        sources = frozenset(
            file.relative_path
            for file in snapshot.files
            if file.classification is FileClassification.SOURCE
        )
        # One pass over history. Each path appears at most once per commit.
        touches = Counter(
            path
            for commit in history.commits
            for path in commit.touched_paths
            if path in sources
        )
        for path in sorted(touches):
            touching = touches[path]
            # Integer comparison: touching / scanned >= min_percent / 100.
            if touching < min_touching or touching * 100 < min_percent * scanned:
                continue
            yield Finding(
                rule_id=self.metadata.id,
                severity=self.metadata.default_severity,
                message=(
                    f"Source file was touched in {touching} of {scanned} scanned commits."
                ),
                path=path,
                evidence={
                    "touching_commits": touching,
                    "scanned_commits": scanned,
                    "requested_commits": history.requested_commits,
                    "minimum_touching_commits": min_touching,
                    "minimum_touch_percent": min_percent,
                    "minimum_scanned_commits": min_scanned,
                    "history_head": history.head_commit,
                    "shallow": history.is_shallow_repository,
                },
            )
