"""SENSITIVE_PROJECT_FILE_CHANGED: changes to an explicit set of project files.

The allowlist is deliberately narrow and path-based. Paths are relative to the
analysis root:

* ``python_packaging``: ``pyproject.toml``, ``setup.py``, ``setup.cfg`` at the
  analysis root only.
* ``swift_package``: ``Package.swift``, ``Package.resolved`` at the analysis
  root only.
* ``xcode_project``: a ``project.pbxproj`` directly inside a directory whose
  name ends in ``.xcodeproj``, at any depth.
* ``github_workflow``: a ``.yml`` or ``.yaml`` file directly inside
  ``.github/workflows/`` at the analysis root.

Matching is exact and case-sensitive.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath

from repo_check.model import Finding, RepositorySnapshot, RuleMetadata, Severity
from repo_check.rules.base import RuleContext

_ROOT_FILES = {
    "pyproject.toml": "python_packaging",
    "setup.py": "python_packaging",
    "setup.cfg": "python_packaging",
    "Package.swift": "swift_package",
    "Package.resolved": "swift_package",
}


def sensitive_category(path: str) -> str | None:
    """The allowlist category of a relative POSIX path, or None."""
    if path in _ROOT_FILES:
        return _ROOT_FILES[path]
    parts = PurePosixPath(path).parts
    if (
        len(parts) >= 2
        and parts[-1] == "project.pbxproj"
        and parts[-2].endswith(".xcodeproj")
    ):
        return "xcode_project"
    if (
        len(parts) == 3
        and parts[0] == ".github"
        and parts[1] == "workflows"
        and parts[2].endswith((".yml", ".yaml"))
    ):
        return "github_workflow"
    return None


class SensitiveProjectFileChanged:
    metadata = RuleMetadata(
        id="SENSITIVE_PROJECT_FILE_CHANGED",
        title="Sensitive project file changed",
        category="change",
        default_severity=Severity.WARNING,
        description=(
            "Reports changes to project build, packaging, dependency, and CI workflow files "
            "from an explicit allowlist (pyproject.toml, setup.py, setup.cfg, Package.swift, "
            "Package.resolved, *.xcodeproj/project.pbxproj, .github/workflows/*.yml|yaml)."
        ),
        remediation=(
            "Review changes to build, dependency, and CI configuration with extra care."
        ),
    )

    def check_change(self, snapshot: RepositorySnapshot, context: RuleContext) -> Iterator[Finding]:
        change_state = snapshot.change
        if change_state is None:
            return
        for change in change_state.changes:
            matched = change.path
            category = sensitive_category(change.path)
            if category is None and change.old_path is not None:
                matched = change.old_path
                category = sensitive_category(change.old_path)
            if category is None:
                continue
            yield Finding(
                rule_id=self.metadata.id,
                severity=self.metadata.default_severity,
                message="Sensitive project file changed.",
                path=change.path,
                evidence={
                    "category": category,
                    "matched_path": matched,
                    "old_path": change.old_path,
                    "status": change.status.value,
                },
            )
