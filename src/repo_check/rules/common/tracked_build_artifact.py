"""TRACKED_BUILD_ARTIFACT: Git-tracked paths that are obvious build artifacts."""

from __future__ import annotations

from collections.abc import Iterator

from repo_check.discovery import is_build_artifact_path
from repo_check.model import FileClassification, Finding, RepositorySnapshot, RuleMetadata, Severity
from repo_check.rules.base import RuleContext


class TrackedBuildArtifact:
    metadata = RuleMetadata(
        id="TRACKED_BUILD_ARTIFACT",
        title="Build artifact tracked by Git",
        category="repository_hygiene",
        default_severity=Severity.WARNING,
        description=(
            "Reports Git-tracked files that centralized classification identifies as build "
            "artifacts (for example files under build/ or dist/, *.egg-info contents, and "
            "compiled outputs such as .o, .pyc, or .class)."
        ),
        remediation=(
            "If the file is not meant to be versioned, remove it from the index "
            "(git rm --cached) and add an ignore rule. If it is tracked intentionally, "
            "add it to repo_check.exclude."
        ),
    )

    def check_repository(
        self, snapshot: RepositorySnapshot, context: RuleContext
    ) -> Iterator[Finding]:
        # Tracked paths are already scoped to the analysis root and filtered
        # by configured exclusions; unavailable Git state has none.
        for path in snapshot.git.tracked_paths:
            if is_build_artifact_path(path):
                yield Finding(
                    rule_id=self.metadata.id,
                    severity=self.metadata.default_severity,
                    message="Build artifact is tracked by Git.",
                    path=path,
                    evidence={
                        "classification": FileClassification.BUILD_ARTIFACT.value,
                        "tracked": True,
                    },
                )
