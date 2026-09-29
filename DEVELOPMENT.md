# RepoCheck Development Instructions

These instructions apply to anyone changing RepoCheck, including maintainers
and contributors. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and the
pull request process.

## Decisions

- Maintainers own product direction, architecture decisions, and merge
  approval.
- Contributors implement focused changes and verify them.

Do not silently redesign accepted architecture. If a change appears to
require changing an architectural decision, raise it explicitly in the issue
or pull request rather than making the change implicitly.

## Product principles

RepoCheck is a deterministic code and repository health analyzer.

The core tool must not require an LLM, AI API, cloud service, or network
access to run.

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

- Keep each change focused on one concern.
- Do not add unrelated features or modify unrelated files.
- Do not weaken, delete, or bypass tests merely to make them pass.
- Do not add runtime dependencies without explicit justification and
  maintainer agreement. Prefer the Python standard library.
- Keep rule behavior and output ordering deterministic.
- Do not add AI/LLM functionality to the analyzer.
- Do not introduce a public plugin framework without a maintainer decision.

## Branches

- `main` is the stable release branch. Changes reach `main` only through
  reviewed release merges from `dev`.
- `dev` is the integration branch. Feature and maintenance work targets
  `dev`, and merges require maintainer approval.

## Verification

Before declaring a change complete:

- run the relevant tests, and the full suite:
  `PYTHONPATH=src python -m unittest discover -s tests`
- run `git diff --check`
- inspect `git status --short`
- report the exact test commands and results
- report any architectural deviations and unresolved risks
