"""Swift rules: policy over facts from ``repo_check.languages.swift``.

These rules never scan source. They obtain facts through
``RuleContext.facts``, which scans each file at most once per analysis.
"""

from __future__ import annotations

from repo_check.languages.swift import SwiftFileFacts
from repo_check.model import DiscoveredFile, FileClassification
from repo_check.rules.base import RuleContext

ELIGIBLE_CLASSIFICATIONS = frozenset({FileClassification.SOURCE, FileClassification.TEST})


def swift_facts(file: DiscoveredFile, context: RuleContext) -> SwiftFileFacts | None:
    """Facts for an eligible source/test Swift file, or None."""
    if file.classification not in ELIGIBLE_CLASSIFICATIONS:
        return None
    return context.facts.swift(file)
