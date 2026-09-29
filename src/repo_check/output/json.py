"""Deterministic JSON output.

Shape (schema_version 1)::

    {
      "schema_version": 1,
      "findings": [
        {
          "rule_id": "FILE_TOO_LARGE",
          "severity": "warning",
          "path": "src/big.py",
          "location": null,
          "message": "...",
          "evidence": {"actual_lines": 1184, "maximum_lines": 1000}
        }
      ]
    }

A repository-scoped finding has ``"path": null`` and ``"location": null``.
Evidence may be nested; mapping keys are sorted at every level and sequence
order is preserved. No timestamps, timing, or absolute paths are emitted.
Finding order is the order given (the engine's sorted order).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from repo_check.model import Finding, Location, canonical_evidence

SCHEMA_VERSION = 1


def render_json(findings: Sequence[Finding]) -> str:
    document = {
        "schema_version": SCHEMA_VERSION,
        "findings": [_finding(finding) for finding in findings],
    }
    return json.dumps(document, indent=2, allow_nan=False) + "\n"


def _finding(finding: Finding) -> dict[str, Any]:
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity.value,
        "path": finding.path,
        "location": _location(finding.location),
        "message": finding.message,
        "evidence": canonical_evidence(finding.evidence),
    }


def _location(location: Location | None) -> dict[str, Any] | None:
    if location is None:
        return None
    return {
        "start_line": location.start_line,
        "end_line": location.end_line,
        "symbol": location.symbol,
    }
