"""FILE_TOO_LARGE: text source/test files longer than a configured maximum."""

from __future__ import annotations

from collections.abc import Iterator

from repo_check.model import DiscoveredFile, FileClassification, Finding, RuleMetadata, Severity
from repo_check.rules.base import RuleContext

# Documentation, configuration, generated, build artifacts, binary, and
# unknown files are routinely long for legitimate reasons, so only code is
# checked.
ELIGIBLE_CLASSIFICATIONS = frozenset({FileClassification.SOURCE, FileClassification.TEST})


class FileTooLarge:
    metadata = RuleMetadata(
        id="FILE_TOO_LARGE",
        title="File too large",
        category="maintainability",
        default_severity=Severity.WARNING,
        description=(
            "Reports source and test files whose line count exceeds the configured maximum."
        ),
        remediation=(
            "Consider whether the file mixes several responsibilities that could be split, "
            "or raise max_lines / exclude the file if its size is intentional."
        ),
    )

    def check_file(self, file: DiscoveredFile, context: RuleContext) -> Iterator[Finding]:
        if file.is_binary or file.line_count is None:
            return
        if file.classification not in ELIGIBLE_CLASSIFICATIONS:
            return
        # Validated as a positive int by config.
        maximum = int(context.settings.options["max_lines"])
        if file.line_count <= maximum:
            return
        yield Finding(
            rule_id=self.metadata.id,
            severity=self.metadata.default_severity,
            message=f"File contains {file.line_count} lines; configured maximum is {maximum}.",
            path=file.relative_path,
            evidence={"actual_lines": file.line_count, "maximum_lines": maximum},
        )
