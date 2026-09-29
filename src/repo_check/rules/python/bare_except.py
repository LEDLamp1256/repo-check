"""PYTHON_BARE_EXCEPT: syntactic ``except:`` clauses."""

from __future__ import annotations

from collections.abc import Iterator

from repo_check.model import DiscoveredFile, Finding, Location, RuleMetadata, Severity
from repo_check.rules.base import RuleContext
from repo_check.rules.python import python_facts


class PythonBareExcept:
    metadata = RuleMetadata(
        id="PYTHON_BARE_EXCEPT",
        title="Python bare except",
        category="correctness",
        default_severity=Severity.WARNING,
        description=(
            "Reports bare 'except:' clauses, which catch every exception including "
            "SystemExit and KeyboardInterrupt."
        ),
        remediation=(
            "Catch the specific exceptions expected, or 'except Exception:' if a broad "
            "handler is intended."
        ),
    )

    def check_file(self, file: DiscoveredFile, context: RuleContext) -> Iterator[Finding]:
        facts = python_facts(file, context)
        if facts is None:
            return
        for handler in facts.except_handlers:
            if not handler.bare:
                continue
            yield Finding(
                rule_id=self.metadata.id,
                severity=self.metadata.default_severity,
                message="Bare 'except:' clause.",
                path=file.relative_path,
                location=Location(start_line=handler.line, symbol=handler.enclosing),
                evidence={"symbol": handler.enclosing},
            )
