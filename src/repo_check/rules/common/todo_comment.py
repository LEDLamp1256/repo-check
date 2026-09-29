"""TODO_COMMENT: TODO/FIXME markers at the start of a code comment.

Rule policy only. Comment recognition (syntaxes, strings, block comments)
lives in ``repo_check.languages.comments``; see that module for exactly what
counts as a comment marker.
"""

from __future__ import annotations

from collections.abc import Iterator

from repo_check.discovery import read_text_lines
from repo_check.languages.comments import extract_comment_markers, has_comment_syntax
from repo_check.model import (
    DiscoveredFile,
    FileClassification,
    Finding,
    Location,
    RuleMetadata,
    Severity,
)
from repo_check.rules.base import RuleContext

ELIGIBLE_CLASSIFICATIONS = frozenset({FileClassification.SOURCE, FileClassification.TEST})
MARKERS = ("TODO", "FIXME")
MAX_TEXT_LENGTH = 120


class TodoComment:
    metadata = RuleMetadata(
        id="TODO_COMMENT",
        title="TODO comment",
        category="maintainability",
        default_severity=Severity.INFO,
        description=(
            "Reports TODO and FIXME markers that begin a comment in source and test files."
        ),
        remediation=(
            "Resolve the work item, or track it elsewhere (for example in an issue) and "
            "remove the marker."
        ),
    )

    def check_file(self, file: DiscoveredFile, context: RuleContext) -> Iterator[Finding]:
        if file.classification not in ELIGIBLE_CLASSIFICATIONS:
            return
        if not has_comment_syntax(file.relative_path):
            return
        lines = read_text_lines(file)
        if lines is None:
            return
        for found in extract_comment_markers(file.relative_path, lines, MARKERS):
            yield Finding(
                rule_id=self.metadata.id,
                severity=self.metadata.default_severity,
                message=f"Comment contains a {found.marker} marker.",
                path=file.relative_path,
                location=Location(start_line=found.line_number),
                evidence={"marker": found.marker, "text": _truncate(found.text)},
            )


def _truncate(text: str) -> str:
    if len(text) > MAX_TEXT_LENGTH:
        return text[: MAX_TEXT_LENGTH - 3].rstrip() + "..."
    return text
