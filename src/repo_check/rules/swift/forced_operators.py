"""SWIFT_FORCE_TRY and SWIFT_FORCE_CAST: uses of ``try!`` and ``as!`` in code."""

from __future__ import annotations

from collections.abc import Iterator

from repo_check.languages.swift import FORCE_CAST, FORCE_TRY
from repo_check.model import DiscoveredFile, Finding, Location, RuleMetadata, Severity
from repo_check.rules.base import RuleContext
from repo_check.rules.swift import swift_facts


class _ForcedOperatorRule:
    metadata: RuleMetadata
    operator: str
    message: str

    def check_file(self, file: DiscoveredFile, context: RuleContext) -> Iterator[Finding]:
        facts = swift_facts(file, context)
        if facts is None:
            return
        for use in facts.operator_uses:
            if use.operator != self.operator:
                continue
            yield Finding(
                rule_id=self.metadata.id,
                severity=self.metadata.default_severity,
                message=self.message,
                path=file.relative_path,
                location=Location(start_line=use.line),
                evidence={"operator": self.operator},
            )


class SwiftForceTry(_ForcedOperatorRule):
    metadata = RuleMetadata(
        id="SWIFT_FORCE_TRY",
        title="Swift force-try",
        category="correctness",
        default_severity=Severity.WARNING,
        description=(
            "Reports uses of the force-try operator 'try!', which terminates the program "
            "if the expression throws."
        ),
        remediation=(
            "Where an error can occur at runtime, consider 'try' with error handling or "
            "'try?'."
        ),
    )
    operator = FORCE_TRY
    message = "Force-try operator 'try!' is used."


class SwiftForceCast(_ForcedOperatorRule):
    metadata = RuleMetadata(
        id="SWIFT_FORCE_CAST",
        title="Swift forced cast",
        category="correctness",
        default_severity=Severity.WARNING,
        description=(
            "Reports uses of the forced cast operator 'as!', which terminates the program "
            "if the cast fails."
        ),
        remediation=(
            "Where the cast can fail at runtime, consider a conditional cast with 'as?'."
        ),
    )
    operator = FORCE_CAST
    message = "Forced cast operator 'as!' is used."
