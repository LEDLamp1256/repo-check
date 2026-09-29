"""PYTHON_MUTABLE_DEFAULT: parameter defaults that are mutable literals.

Deliberately high precision: only list/dict/set displays and list/dict/set
comprehensions are reported. Calls such as ``list()`` or ``defaultdict()``
are not, because the called name may be shadowed.
"""

from __future__ import annotations

from collections.abc import Iterator

from repo_check.model import DiscoveredFile, Finding, Location, RuleMetadata, Severity
from repo_check.rules.base import RuleContext
from repo_check.rules.python import python_facts

# AST node class name -> default kind reported in evidence and messages.
MUTABLE_DEFAULT_KINDS = {
    "List": "list",
    "Dict": "dict",
    "Set": "set",
    "ListComp": "list_comprehension",
    "DictComp": "dict_comprehension",
    "SetComp": "set_comprehension",
}


class PythonMutableDefault:
    metadata = RuleMetadata(
        id="PYTHON_MUTABLE_DEFAULT",
        title="Python mutable default argument",
        category="correctness",
        default_severity=Severity.WARNING,
        description=(
            "Reports function parameters whose default is a list, dict, or set literal or "
            "comprehension. The default is created once, when the function is defined, and "
            "shared by every call."
        ),
        remediation=(
            "Use None as the default and create the value inside the function."
        ),
    )

    def check_file(self, file: DiscoveredFile, context: RuleContext) -> Iterator[Finding]:
        facts = python_facts(file, context)
        if facts is None:
            return
        for default in facts.parameter_defaults:
            kind = MUTABLE_DEFAULT_KINDS.get(default.default_node)
            if kind is None:
                continue
            yield Finding(
                rule_id=self.metadata.id,
                severity=self.metadata.default_severity,
                message=(
                    f"Parameter '{default.parameter}' of {default.function} has a mutable "
                    f"default ({kind.replace('_', ' ')})."
                ),
                path=file.relative_path,
                location=Location(start_line=default.line, symbol=default.function),
                evidence={
                    "default_kind": kind,
                    "parameter": default.parameter,
                    "parameter_kind": default.parameter_kind.value,
                    "symbol": default.function,
                },
            )
