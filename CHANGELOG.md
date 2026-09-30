# Changelog

## 0.1.0 - 2026-09-29

First public release of RepoCheck, a deterministic code and repository health
analyzer. It runs locally, needs no network access or cloud service, and has
no third-party runtime dependencies. It requires Python 3.11 or newer.

### Analysis

- Fourteen available rules with stable IDs:
  - Files and repository: `FILE_TOO_LARGE`, `TODO_COMMENT`,
    `BROKEN_LOCAL_DOC_LINK` (relative links in Markdown; opt-in, see below),
    and `TRACKED_BUILD_ARTIFACT` (build outputs tracked by Git).
  - Python, using the standard library `ast` module (code is never run):
    `PYTHON_FUNCTION_TOO_LARGE`, `PYTHON_CLASS_TOO_LARGE`,
    `PYTHON_BARE_EXCEPT`, and `PYTHON_MUTABLE_DEFAULT`.
  - Swift, using a conservative lexical scanner: `SWIFT_FORCE_TRY` and
    `SWIFT_FORCE_CAST`.
  - Branch comparison, with `--compare REF`: `LARGE_CHANGESET`,
    `PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE`, and
    `SENSITIVE_PROJECT_FILE_CHANGED`.
  - History, with `--history N`: `FREQUENTLY_CHANGED_FILE` (info), which
    reports current source files touched by at least 10 and at least 10% of
    the scanned commits, once at least 50 commits were scanned. All three
    thresholds are configurable.
- Rules favor precision: a missed low-value finding is preferred over a
  recurring false positive.
- All rules are enabled by default except `BROKEN_LOCAL_DOC_LINK`, which is
  opt-in (`enabled = true` in its rule table). It resolves links as
  filesystem paths, which is accurate for plain Markdown but produces
  frequent false positives on documentation built by static-site generators,
  whose link semantics differ.
- Generic `build/` and `dist/` directories are treated as build output only
  at the top level of the analyzed path. Nested directories with these names
  are often ordinary source or documentation, so they are classified and
  analyzed normally. `.build/`, `*.egg-info`, and compiled outputs are build
  artifacts at any depth.
- `--compare REF` analyzes the changes committed on the current branch since
  its merge base with a local ref.
- `--history N` collects at most `N` local non-merge commits reachable from
  `HEAD`, in a pinned, deterministic order, for the history rules.

### Configuration and output

- Optional `repo-check.toml` (or `--config FILE`) for exclusions and
  per-rule settings, validated strictly.
- Text and JSON output (`schema_version` 1) with deterministic finding order
  and no timestamps or absolute paths.
- Exit status 0 (success), 1 (a finding met `--fail-on`), 2 (execution or
  configuration error).

### Git

- Git is optional except for `--compare` and `--history`. RepoCheck reads
  only local state and never fetches.
- Git runs without a shell, with a timeout. Repository-redirecting
  environment variables, `core.fsmonitor`, `git replace` overlays, and
  submodule-ignore settings cannot change the results.

### Known limitations

- Only committed changes are compared; staged and unstaged changes are not
  analyzed.
- `--history` does not follow renames: a renamed file counts as its old path
  and its new path.
- The Swift scanner is lexical, not a parser. It misses some uses (for
  example in string interpolation or near ambiguous `/`) to avoid false
  positives, and it scans inactive `#if` branches.
- Classification and `--compare` scope are relative to the analyzed path,
  so tests outside that path do not count as test changes.
- Markdown support is a conservative subset. Documents inside DocC catalogs
  (`*.docc`) are not link-checked, and anchors are not validated.
- Files that no longer exist are classified from their path alone.
