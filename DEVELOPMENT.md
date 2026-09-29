# RepoCheck Development Instructions

## Roles

This project uses a deliberate architecture/implementation split.

- Maintainers own product architecture, sequencing, task definitions, architectural review, and merge-readiness decisions.
- Contributors own repository-native implementation, edits, tests, builds, and verification for explicitly assigned tasks.
- The project owner owns product intent, important architecture decisions, and merge approval.

Do not silently redesign accepted architecture.

If an assigned task appears to require changing an architectural decision, stop and report the conflict rather than making the change implicitly.

## Product principles

RepoCheck is a deterministic code and repository health analyzer.

The core tool must not require an LLM, AI API, or other cloud service to run.

Prioritize:

- deterministic behavior
- high-confidence findings
- explainable evidence
- stable rule identifiers
- low false-positive rates
- small coherent changes
- strong fixture-based tests

A missed low-value finding is preferable to a recurring false positive.

## Development discipline

- Work only on the assigned task.
- Do not add unrelated features.
- Do not weaken, delete, or bypass tests merely to make them pass.
- Do not add dependencies without explicit architectural justification or task authorization.
- Do not modify unrelated files.
- Keep rule behavior deterministic.
- Keep output ordering deterministic where applicable.
- Prefer the Python standard library where practical.
- Do not add AI/LLM functionality to the analyzer.
- Do not introduce a public plugin framework unless explicitly assigned.
- Do not merge to `main`.
- Do not merge feature work into `dev` without maintainer approval.

## Verification

Before declaring an implementation complete:

- run all relevant tests
- run `git diff --check`
- inspect `git status --short`
- report exact test commands and results
- report any architectural deviations
- report genuine unresolved risks

A clean implementation report is part of task completion.
