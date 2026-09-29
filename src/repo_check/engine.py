"""Analysis pipeline: configuration -> Git facts -> discovery -> rules -> sorted findings."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from repo_check.config import load_config
from repo_check.discovery import discover, resolve_root
from repo_check.errors import RepoCheckError
from repo_check.git import read_git_comparison, read_git_history, read_git_state
from repo_check.languages.facts import AnalysisFacts
from repo_check.model import Finding, RepositorySnapshot, sort_findings
from repo_check.rules import (
    CHANGE_RULES,
    FILE_RULES,
    HISTORY_RULES,
    REPOSITORY_RULES,
    ChangeRule,
    FileRule,
    HistoryRule,
    RepositoryRule,
    RuleContext,
)


@dataclass(frozen=True)
class AnalysisResult:
    snapshot: RepositorySnapshot
    findings: tuple[Finding, ...]


def analyze(
    root: Path,
    config_path: Path | None = None,
    file_rules: Sequence[FileRule] = FILE_RULES,
    repository_rules: Sequence[RepositoryRule] = REPOSITORY_RULES,
    change_rules: Sequence[ChangeRule] = CHANGE_RULES,
    compare_ref: str | None = None,
    history_commits: int | None = None,
    history_rules: Sequence[HistoryRule] = HISTORY_RULES,
) -> AnalysisResult:
    """Run the analysis. ``compare_ref`` requests comparison change analysis
    against ``merge-base(compare_ref, HEAD)``; any failure to compute it
    raises ``ComparisonError``. ``history_commits`` requests bounded history
    analysis of at most that many non-merge commits reachable from HEAD; any
    failure to read it raises ``HistoryError``."""
    config = load_config(root, config_path)
    resolved_root = resolve_root(root)
    git_state = read_git_state(resolved_root)
    comparison = (
        read_git_comparison(resolved_root, compare_ref) if compare_ref is not None else None
    )
    history = (
        read_git_history(resolved_root, history_commits) if history_commits is not None else None
    )
    snapshot = discover(root, config, git_state, comparison, history)
    findings = run_rules(snapshot, file_rules, repository_rules, change_rules, history_rules)
    return AnalysisResult(snapshot=snapshot, findings=findings)


def run_rules(
    snapshot: RepositorySnapshot,
    file_rules: Sequence[FileRule] = (),
    repository_rules: Sequence[RepositoryRule] = (),
    change_rules: Sequence[ChangeRule] = (),
    history_rules: Sequence[HistoryRule] = (),
) -> tuple[Finding, ...]:
    """Run enabled file rules over every discovered file, enabled repository
    rules once over the snapshot, (only when the snapshot has change facts)
    enabled change rules once, and (only when it has history facts) enabled
    history rules once, then sort all findings."""
    findings: list[Finding] = []
    facts = AnalysisFacts()  # shared by every rule in this run
    for rule in file_rules:
        context = _context(snapshot, rule.metadata.id, facts)
        if context is None:
            continue
        for file in snapshot.files:
            _collect(rule.metadata.id, rule.check_file(file, context), findings)
    for repository_rule in repository_rules:
        context = _context(snapshot, repository_rule.metadata.id, facts)
        if context is None:
            continue
        _collect(
            repository_rule.metadata.id,
            repository_rule.check_repository(snapshot, context),
            findings,
        )
    if snapshot.change is not None:
        for change_rule in change_rules:
            context = _context(snapshot, change_rule.metadata.id, facts)
            if context is None:
                continue
            _collect(change_rule.metadata.id, change_rule.check_change(snapshot, context), findings)
    if snapshot.history is not None:
        for history_rule in history_rules:
            context = _context(snapshot, history_rule.metadata.id, facts)
            if context is None:
                continue
            _collect(
                history_rule.metadata.id, history_rule.check_history(snapshot, context), findings
            )
    return sort_findings(findings)


def _context(
    snapshot: RepositorySnapshot, rule_id: str, facts: AnalysisFacts
) -> RuleContext | None:
    settings = snapshot.config.rule(rule_id)
    return RuleContext(settings=settings, facts=facts) if settings.enabled else None


def _collect(rule_id: str, produced: Iterable[Finding], findings: list[Finding]) -> None:
    for finding in produced:
        if finding.rule_id != rule_id:
            raise RepoCheckError(f"rule {rule_id} produced a finding for {finding.rule_id}")
        findings.append(finding)
