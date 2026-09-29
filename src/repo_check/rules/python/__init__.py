"""Python rules: policy over facts from ``repo_check.languages.python``.

These rules never parse source. They obtain facts through
``RuleContext.facts``, which parses each file at most once per analysis.
"""

from __future__ import annotations

from repo_check.languages.python import PythonModuleFacts
from repo_check.model import DiscoveredFile, FileClassification
from repo_check.rules.base import RuleContext

ELIGIBLE_CLASSIFICATIONS = frozenset({FileClassification.SOURCE, FileClassification.TEST})


def python_facts(file: DiscoveredFile, context: RuleContext) -> PythonModuleFacts | None:
    """Facts for an eligible source/test Python file, or None.

    None also covers files that do not parse; Python rules skip them.
    """
    if file.classification not in ELIGIBLE_CLASSIFICATIONS:
        return None
    return context.facts.python(file)
