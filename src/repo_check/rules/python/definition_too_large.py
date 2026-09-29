"""PYTHON_FUNCTION_TOO_LARGE and PYTHON_CLASS_TOO_LARGE."""

from __future__ import annotations

from collections.abc import Iterator

from repo_check.languages.python import DefinitionKind
from repo_check.model import DiscoveredFile, Finding, Location, RuleMetadata, Severity
from repo_check.rules.base import RuleContext
from repo_check.rules.python import python_facts


class _DefinitionTooLarge:
    metadata: RuleMetadata
    kinds: frozenset[DefinitionKind]
    noun: str

    def check_file(self, file: DiscoveredFile, context: RuleContext) -> Iterator[Finding]:
        facts = python_facts(file, context)
        if facts is None:
            return
        # Validated as a positive int by config.
        maximum = int(context.settings.options["max_lines"])
        for definition in facts.definitions:
            if definition.kind not in self.kinds or definition.line_count <= maximum:
                continue
            yield Finding(
                rule_id=self.metadata.id,
                severity=self.metadata.default_severity,
                message=(
                    f"{self.noun} {definition.qualified_name} spans {definition.line_count} "
                    f"lines; configured maximum is {maximum}."
                ),
                path=file.relative_path,
                location=Location(
                    start_line=definition.start_line,
                    end_line=definition.end_line,
                    symbol=definition.qualified_name,
                ),
                evidence={
                    "actual_lines": definition.line_count,
                    "maximum_lines": maximum,
                    "symbol": definition.qualified_name,
                },
            )


class PythonFunctionTooLarge(_DefinitionTooLarge):
    metadata = RuleMetadata(
        id="PYTHON_FUNCTION_TOO_LARGE",
        title="Python function too large",
        category="maintainability",
        default_severity=Severity.WARNING,
        description=(
            "Reports Python functions and async functions whose definition spans more "
            "lines than the configured maximum (def line through last line, inclusive)."
        ),
        remediation=(
            "Consider splitting the function into smaller functions, or raise max_lines "
            "if its size is intentional."
        ),
    )
    kinds = frozenset({DefinitionKind.FUNCTION, DefinitionKind.ASYNC_FUNCTION})
    noun = "Function"


class PythonClassTooLarge(_DefinitionTooLarge):
    metadata = RuleMetadata(
        id="PYTHON_CLASS_TOO_LARGE",
        title="Python class too large",
        category="maintainability",
        default_severity=Severity.WARNING,
        description=(
            "Reports Python classes whose definition spans more lines than the configured "
            "maximum (class line through last line, inclusive)."
        ),
        remediation=(
            "Consider splitting the class, or raise max_lines if its size is intentional."
        ),
    )
    kinds = frozenset({DefinitionKind.CLASS})
    noun = "Class"
