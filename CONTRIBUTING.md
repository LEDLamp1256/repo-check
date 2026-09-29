# Contributing to RepoCheck

Thank you for your interest in improving RepoCheck.

## Project principles

RepoCheck is a deterministic code and repository health analyzer. Changes
should keep it:

- **Deterministic.** The same inputs produce the same findings, in the same
  order, with the same output.
- **Explainable.** Every finding carries evidence a reader can verify.
- **Low in false positives.** A missed low-value finding is preferable to a
  recurring false positive.
- **Stable.** Rule identifiers and the JSON output schema are part of the
  public interface.
- **Local.** The core analyzer must not require an LLM, cloud service, or
  network access.

## Development setup

RepoCheck requires Python 3.11 or newer and has no third-party runtime
dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Before opening a pull request

Run the test suite and the whitespace check:

```bash
PYTHONPATH=src python -m unittest discover -s tests
git diff --check
```

- Add or update tests for any behavior change. Fixture-based tests are
  preferred for rule behavior.
- Do not weaken, delete, or skip tests to make a change pass.
- Preserve deterministic behavior and output ordering.
- Keep changes small and focused on one concern.

## Pull requests and branches

- `main` is the stable release branch.
- `dev` is the integration branch. Open feature and maintenance pull requests
  against `dev`.
- Describe what changed, why, and how it was tested.

## Dependencies and architecture

- Discuss any new runtime dependency in an issue before introducing it. The
  standard library is preferred.
- Make architectural changes explicit: explain them in the pull request
  rather than folding them into an unrelated change.
- New rules should favor precision, document their behavior in the README,
  and use a stable rule identifier.

## Security issues

Do not report security vulnerabilities in public issues or pull requests.
See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
Apache License 2.0, as described in [LICENSE](LICENSE).
