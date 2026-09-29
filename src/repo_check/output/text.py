"""Plain, deterministic text output.

Example::

    src/big.py: warning FILE_TOO_LARGE: File contains 1184 lines; configured maximum is 1000.
      evidence: actual_lines=1184, maximum_lines=1000

    1 finding (0 error, 1 warning, 0 info).

Repository-scoped findings (no path) are shown as ``<repository>``. Evidence
values use compact JSON syntax with sorted mapping keys, e.g.
``files=["a.py", "b.py"]`` or ``span={"end": 9, "start": 3}``.

Paths, symbols, and messages come from the analyzed repository, so control
characters (which could break the one-finding-per-line layout or drive a
terminal) and lone surrogates (undecodable bytes in file names) are shown as
backslash escapes such as ``\\n`` or ``\\udcff``. JSON output is exact.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from repo_check.model import REPOSITORY_PATH_LABEL, Finding, Severity, canonical_evidence


def render_text(findings: Sequence[Finding]) -> str:
    if not findings:
        return "No findings.\n"

    lines: list[str] = []
    for finding in findings:
        lines.append(
            f"{_where(finding)}: {finding.severity.value} {finding.rule_id}: "
            f"{_escape(finding.message)}"
        )
        evidence = canonical_evidence(finding.evidence)
        if evidence:
            pairs = ", ".join(f"{key}={_value(value)}" for key, value in evidence.items())
            lines.append(f"  evidence: {pairs}")

    counts = {severity: 0 for severity in Severity}
    for finding in findings:
        counts[finding.severity] += 1
    noun = "finding" if len(findings) == 1 else "findings"
    lines.append("")
    lines.append(
        f"{len(findings)} {noun} ({counts[Severity.ERROR]} error, "
        f"{counts[Severity.WARNING]} warning, {counts[Severity.INFO]} info)."
    )
    return "\n".join(lines) + "\n"


def _where(finding: Finding) -> str:
    if finding.path is None:
        return REPOSITORY_PATH_LABEL
    path = _escape(finding.path)
    location = finding.location
    if location is None:
        return path
    where = f"{path}:{location.start_line}"
    if location.end_line is not None and location.end_line != location.start_line:
        where += f"-{location.end_line}"
    if location.symbol:
        where += f" ({_escape(location.symbol)})"
    return where


def _value(value: object) -> str:
    # JSON syntax: strings are quoted so embedded commas stay unambiguous.
    # Evidence is already canonical (sorted keys, finite numbers).
    return json.dumps(value, allow_nan=False)


_NAMED_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape(text: str) -> str:
    """Escape C0/C1 control characters, DEL, and lone surrogates."""
    return "".join(_escape_char(char) for char in text)


def _is_unsafe(char: str) -> bool:
    code = ord(char)
    return code < 0x20 or 0x7F <= code <= 0x9F or 0xD800 <= code <= 0xDFFF


def _escape_char(char: str) -> str:
    if not _is_unsafe(char):
        return char
    if char in _NAMED_ESCAPES:
        return _NAMED_ESCAPES[char]
    code = ord(char)
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}"
