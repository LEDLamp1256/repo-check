# RepoCheck

RepoCheck is a deterministic code and repository health analyzer.

The core analyzer does not depend on an LLM, AI API, or cloud service, and
makes no network access during analysis. Given the same analyzed files,
configuration, and inputs (including Git tracked state, when Git is
available), output and finding order are identical from run to run. Results
from Git-based rules depend on the local Git state: they differ if files are
tracked differently, and are empty when Git is unavailable.

Development version: 0.2.0.dev0. The latest tagged release is 0.1.0.

Fourteen rules are available: `FILE_TOO_LARGE`,
`TODO_COMMENT`, `BROKEN_LOCAL_DOC_LINK`, `TRACKED_BUILD_ARTIFACT`, four
Python rules (`PYTHON_FUNCTION_TOO_LARGE`, `PYTHON_CLASS_TOO_LARGE`,
`PYTHON_BARE_EXCEPT`, `PYTHON_MUTABLE_DEFAULT`), two Swift rules
(`SWIFT_FORCE_TRY`, `SWIFT_FORCE_CAST`), and three branch-comparison rules
(`LARGE_CHANGESET`, `PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE`,
`SENSITIVE_PROJECT_FILE_CHANGED`) used with `--compare`, and one history rule
(`FREQUENTLY_CHANGED_FILE`) used with `--history N`. All rules are enabled by
default except `BROKEN_LOCAL_DOC_LINK`, which must be enabled in the
configuration. Deeper Swift analysis and uncommitted-change analysis are
future work.

## Requirements

- Python 3.11 or newer
- No third-party runtime dependencies (standard library only)
- Optional: a `git` executable on `PATH`, used only to read which files are
  tracked (see [Git support](#git-support)), the committed changes on the
  current branch with `--compare`, and local commit history with `--history`.
  Git is required when `--compare` or `--history` is given

Continuous integration is configured to run the test suite on Ubuntu with
Python 3.11, 3.12, 3.13, and 3.14, and on macOS with Python 3.14. Other
platforms and versions are not tested. See [CHANGELOG.md](CHANGELOG.md) for
release notes.

## Installation

RepoCheck is not published to PyPI. Install it from a checkout of this
repository into a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
repo-check --version          # prints: repo-check 0.2.0.dev0
```

This installs the `repo-check` command; `python -m repo_check` is
equivalent. After installation the checkout is not needed to run it.

To install a built artifact instead, build a wheel and install that file:

```bash
python -m pip install build
python -m build                # writes dist/repo_check-0.2.0.dev0-py3-none-any.whl and a .tar.gz
python -m pip install dist/repo_check-0.2.0.dev0-py3-none-any.whl
```

### Development install

For working on RepoCheck itself, use an editable install, so changes to the
source take effect without reinstalling:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Without installing, you can also run it from a checkout with
`PYTHONPATH=src python -m repo_check PATH`.

## Usage

```bash
repo-check .                         # analyze the current directory, text output
repo-check . --format json           # structured JSON output
repo-check . --fail-on warning       # exit 1 if any warning or error is found
repo-check . --compare dev           # also analyze this branch's committed changes
repo-check . --compare main --format json
repo-check Sources --compare HEAD~3  # changes under Sources/ in the last 3 commits
repo-check . --history 200           # also analyze up to 200 non-merge commits of history
```

Text output:

```text
src/big.py: warning FILE_TOO_LARGE: File contains 1184 lines; configured maximum is 1000.
  evidence: actual_lines=1184, maximum_lines=1000

1 finding (0 error, 1 warning, 0 info).
```

With no findings, text output is `No findings.`

JSON output (`schema_version` 1):

```json
{
  "schema_version": 1,
  "findings": [
    {
      "rule_id": "FILE_TOO_LARGE",
      "severity": "warning",
      "path": "src/big.py",
      "location": null,
      "message": "File contains 1184 lines; configured maximum is 1000.",
      "evidence": {
        "actual_lines": 1184,
        "maximum_lines": 1000
      }
    }
  ]
}
```

Paths are relative to the analyzed directory and use `/` separators. Output
contains no timestamps, timings, or absolute paths. In text output, control
characters (such as a newline in a file name) and bytes that are not valid
UTF-8 are shown as backslash escapes (`\n`, `\udcff`), and characters the
output stream cannot encode are written as backslash escapes; JSON output is
exact and ASCII-only.

Some findings apply to no single file (`LARGE_CHANGESET` and
`PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE`). These repository-scoped findings
have `"path": null` in JSON and are shown as `<repository>` in text. Evidence
is JSON-compatible data and may be nested; mapping keys are sorted at every
level and list order is preserved.

Findings are sorted with repository-scoped findings first, then by path, then
start line (findings without a location first), then rule ID, then message.
Text and JSON use the same order.

### Exit status and `--fail-on`

| Status | Meaning |
| --- | --- |
| 0 | Analysis succeeded and no finding met the `--fail-on` level |
| 1 | At least one finding met the `--fail-on` level |
| 2 | Execution or configuration error (bad path, invalid config, bad arguments, or a `--compare` or `--history` that cannot be carried out) |

`--fail-on` accepts `info`, `warning`, or `error` and defaults to `error`, so
warnings and informational findings are reported without failing the run.
Use `--fail-on warning` to fail CI on warnings.

## Configuration

RepoCheck reads `repo-check.toml` from the root of the analyzed directory if
it exists, or the file given with `--config FILE` (which takes precedence).
Without either, built-in defaults apply.

```toml
[repo_check]
# Glob patterns matched against the path relative to the repository root.
exclude = ["vendor/", "third_party/", "*.min.js"]

[repo_check.rules.FILE_TOO_LARGE]
enabled = true     # default: true
max_lines = 1000   # default: 1000; must be a positive integer

[repo_check.rules.TODO_COMMENT]
enabled = true     # default: true

[repo_check.rules.BROKEN_LOCAL_DOC_LINK]
enabled = false    # default: false; set to true to enable the rule

[repo_check.rules.TRACKED_BUILD_ARTIFACT]
enabled = true     # default: true; set to false to disable the rule
```

`FILE_TOO_LARGE`, `PYTHON_FUNCTION_TOO_LARGE`, and `PYTHON_CLASS_TOO_LARGE`
also accept `max_lines`, `LARGE_CHANGESET` accepts `max_files`, and
`FREQUENTLY_CHANGED_FILE` accepts the three thresholds shown; the other rules
accept only `enabled`:

```toml
[repo_check.rules.PYTHON_FUNCTION_TOO_LARGE]
enabled = true
max_lines = 100    # default: 100

[repo_check.rules.PYTHON_CLASS_TOO_LARGE]
enabled = true
max_lines = 500    # default: 500

[repo_check.rules.PYTHON_BARE_EXCEPT]
enabled = true

[repo_check.rules.PYTHON_MUTABLE_DEFAULT]
enabled = true

[repo_check.rules.SWIFT_FORCE_TRY]
enabled = true

[repo_check.rules.SWIFT_FORCE_CAST]
enabled = true

[repo_check.rules.LARGE_CHANGESET]
enabled = true
max_files = 50     # default: 50; must be a positive integer

[repo_check.rules.PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE]
enabled = true

[repo_check.rules.SENSITIVE_PROJECT_FILE_CHANGED]
enabled = true

[repo_check.rules.FREQUENTLY_CHANGED_FILE]
enabled = true
min_touching_commits = 10  # default: 10; must be a positive integer
min_touch_percent = 10     # default: 10; an integer from 1 to 100
min_scanned_commits = 50   # default: 50; must be a positive integer
```

Configuration is validated strictly. Malformed TOML, unknown keys, unknown
rule IDs, and values of the wrong type (for example `max_lines = "1000"`)
stop the run with exit status 2 rather than being ignored or coerced.

### Exclusions

- Each pattern is matched with shell-style globbing (`fnmatch`) against the
  full POSIX path relative to the repository root. `*` also matches `/`, so
  `*.min.js` matches at any depth.
- A pattern that matches a directory excludes everything beneath it.
  `vendor` matches only the top-level `vendor`; use `*/vendor` for nested ones.
- A trailing `/` (for example `vendor/`) restricts the pattern to directories.
- Excluded files are never read.
- Exclusions also apply to Git-tracked paths, so an excluded tracked file is
  never reported by `TRACKED_BUILD_ARTIFACT`.

### Built-in discovery behavior

- VCS metadata (`.git`, `.hg`, `.svn`) is skipped, and dependency/cache
  directories (`__pycache__`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`,
  `.tox`, `.nox`, `.venv`, `node_modules`, `.build`) are never entered.
- Symlinks are never followed, whether they point to files or directories,
  inside or outside the repository.
- A file is treated as binary if it has a known binary extension or contains
  a NUL byte in its first 8 KiB.
- Each file is classified once, conservatively, as source, test,
  documentation, configuration, generated, build artifact, binary, or
  unknown. Build artifacts are files under a top-level `build/` or `dist/`
  directory (the first directory relative to the analyzed path), under a
  `.build/` or `*.egg-info` directory at any depth, and compiled outputs
  (see [`TRACKED_BUILD_ARTIFACT`](#tracked_build_artifact-warning)). Nested
  directories named `build` or `dist`, such as `docs/build/` or
  `src/pkg/dist/`, are classified like any other directory. Lockfiles,
  minified assets, and files with an `@generated` or `DO NOT EDIT` marker in
  their first five lines are treated as generated.

### Git support

Git is optional. When the analyzed directory is inside a Git worktree and a
`git` executable is available, RepoCheck reads the list of tracked files with
`git ls-files`. It only reads local state: no remotes, fetching, or network
access. Commit history is read only for an explicit `--compare` (see
[Branch comparison](#branch-comparison---compare-ref)) or `--history` (see
[History (`--history N`)](#history---history-n)).

- Directories that are not Git repositories, or machines without `git`, are
  analyzed normally; rules that need Git state simply report nothing. (An
  explicit `--compare` or `--history` is the exception: it requires Git and
  fails with exit status 2 when Git state is unavailable.)
- When the analyzed directory is a subdirectory of a larger worktree, only
  tracked files beneath it are considered, with paths relative to it.
- Tracked files are known independently of the filesystem walk, so a tracked
  file inside a directory that discovery skips (for example `__pycache__`) is
  still visible to Git-based rules.
- Environment variables such as `GIT_DIR` and `GIT_WORK_TREE` are ignored, so
  results depend only on the analyzed path.
- Local `git replace` overlays are ignored, so comparison and history facts
  correspond to the actual commit objects named by their SHAs.

## Branch comparison (`--compare REF`)

`repo-check PATH --compare REF` runs the normal analysis and also analyzes the
changes committed on the current branch. It compares `HEAD` with the merge
base of `REF` and `HEAD` (the same range as `git diff REF...HEAD`), so commits
made on `REF` after the branch point are not counted.

- Only committed changes are analyzed. Unstaged and staged-but-uncommitted
  changes are not included.
- `REF` must already resolve to a commit in the local repository (a branch,
  remote-tracking branch such as `origin/main`, tag, or commit). RepoCheck
  never fetches, contacts a remote, or uses credentials.
- Only changes beneath `PATH` are analyzed, with paths relative to `PATH`. A
  file renamed into `PATH` from outside counts as added; one renamed out of it
  counts as deleted.
- Added, modified, deleted, renamed, copied, and type-changed files are all
  changes. Git's rename and copy detection is used, and a rename or copy counts
  as one changed file.
- `repo_check.exclude` applies: a change is ignored when every path it
  touches is excluded.
- Changed files are classified the same way as elsewhere (source, test,
  documentation, and so on). Files that no longer exist are classified from
  their path alone.
- Path-based classification is relative to `PATH`, and only changes beneath
  `PATH` count, so tests outside `PATH` are not test changes. For example,
  `repo-check src --compare main` may report
  `PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE` even though `tests/` changed. For
  project-wide production-versus-test analysis, run RepoCheck from the
  repository root.
- Changed submodule pointers are always reported as changes, regardless of
  `diff.ignoreSubmodules` or `submodule.<name>.ignore` settings.

If `--compare` is given and the comparison cannot be made, RepoCheck exits
with status 2 and an error message rather than reporting no change findings.
This covers: Git not installed, `PATH` not inside a Git worktree, a `REF` that
does not resolve to a local commit (including ranges such as `a..b`, and refs
beginning with `-`), no merge base between `REF` and `HEAD`, and a failing Git
command.

Without `--compare`, no Git comparison is made and the change rules do not
run.

## History (`--history N`)

`repo-check PATH --history N` runs the normal analysis and also collects the
local commit history of `HEAD` for the history rules (see
[History rules](#history-rules---history-only)). Without `--history`, no
history is read and history rules do not run.

- `N` must be a positive integer. At most `N` commits are read: non-merge
  commits reachable from the resolved `HEAD`, in Git's explicitly pinned
  `--topo-order` traversal. Merge commits are skipped and do not count toward
  `N`. There is no default depth, and history is never read unless
  requested.
- For each commit, RepoCheck records the paths it added, modified, deleted, or
  changed the type of. Renames are not followed: a renamed file counts as its
  old path and its new path.
- Only paths beneath `PATH` are recorded, relative to `PATH`, and
  `repo_check.exclude` applies to them. Commits that touch nothing beneath
  `PATH` still count toward `N`.
- Only local objects are read; RepoCheck never fetches. In a shallow clone,
  only the locally available commits are read.
- `--history` can be combined with `--compare`.

If `--history` is given and history cannot be read, RepoCheck exits with
status 2 and an error message. This covers: an invalid `N`, Git not installed,
`PATH` not inside a Git worktree, a repository with no commits, and a failing
Git command.

## Rules

### `FILE_TOO_LARGE` (warning)

Reports source and test files with more than `max_lines` lines (default
1000). Documentation, configuration, generated files, build artifacts, binary
files, and unclassified files are not checked. A large file is reported as a
fact, not as proof of bad design; raise `max_lines` or exclude the file if the
size is intentional.

### `TODO_COMMENT` (info)

Reports `TODO` and `FIXME` markers that begin a comment in source and test
files:

```text
src/app.py:12: info TODO_COMMENT: Comment contains a TODO marker.
  evidence: marker="TODO", text="TODO(alice): handle timeouts"
```

Detection is deliberately conservative:

- Comments are recognized by extension: `#` comments (Python, shell, Ruby,
  Perl, Elixir) and `//` / `/* */` comments (C, C++, Objective-C, C#, Rust,
  Swift, Java, Kotlin, Scala, Go, JavaScript, TypeScript, Dart). Other files
  are not checked.
- The marker must be the uppercase word `TODO` or `FIXME` and the first word
  of the comment. Mentions later in a comment ("see the TODO list"),
  lowercase `todo`, and identifiers such as `TODO_ITEMS` are not reported.
- Markers inside string literals, docstrings, and multi-line strings are not
  reported.

Findings are `info`, so they do not affect the exit status unless
`--fail-on info` is used.

### `BROKEN_LOCAL_DOC_LINK` (warning)

Reports relative links in Markdown documentation (`.md`, `.markdown`) whose
target does not exist in the analyzed directory.

This rule is available but disabled by default in v0.1. RepoCheck resolves
these links as filesystem paths. Documentation managed by static-site
generators may use different link semantics. Enable the rule when
filesystem-relative Markdown links match the project's conventions, or
exclude generator-managed documentation as appropriate:

```toml
[repo_check.rules.BROKEN_LOCAL_DOC_LINK]
enabled = true
```

When enabled, it reports each missing target:

```text
README.md:3: warning BROKEN_LOCAL_DOC_LINK: Local link target does not exist: docs/missing.md
  evidence: link="docs/missing.md", resolved_path="docs/missing.md"
```

- Inline links and images (`[text](path)`, `![alt](path)`) and reference
  definitions (`[id]: path`) are checked. Links are resolved relative to the
  document; `#fragment` and `?query` parts are ignored, so anchors are not
  validated.
- Markdown files inside a DocC catalog (a `*.docc` directory, used by Swift
  packages) are not checked: DocC finds images by file name anywhere in the
  catalog and turns relative links into hosted documentation URLs, so their
  targets are not file paths. Links into a catalog from other documents are
  checked normally.
- Not checked: URLs (`https:`, `mailto:`, ...), `//host` links,
  root-absolute `/path` links, same-page `#anchor` links, templated targets
  (containing `{`, `}`, or `$`), bare e-mail addresses, and links that point
  above the analyzed directory.
- Links inside fenced code blocks, inline code, and HTML comments are
  ignored, as are lines indented by 4 or more spaces or a tab (possible
  indented code; this also skips deeply indented list content).
- A target inside a directory that discovery skips or excludes (for example
  `node_modules/`, or a path in `repo_check.exclude`) or behind a symlink is
  treated as unknown and never reported.
- Documentation-site links are accepted: `guide` or `guide.html` counts as
  existing when `guide.md` exists.
- A link that differs from an existing path only by letter case is reported
  (it breaks on case-sensitive systems), with the existing path in
  `case_insensitive_match` evidence.

### Python rules

The Python rules analyze source and test files ending in `.py` or `.pyi`
using the standard library `ast` module. Each file is parsed at most once per
run, and analyzed code is never imported or executed.

- A file that the running Python cannot parse (a syntax error, or syntax
  newer than the interpreter running RepoCheck) is skipped by the Python
  rules; other rules still check it. Results can therefore differ between
  Python versions for files that use version-specific syntax.
- Symbols use Python's qualified-name convention: `Outer.method`,
  `func.<locals>.helper`.

#### `PYTHON_FUNCTION_TOO_LARGE` (warning)

Reports a `def` or `async def` (including methods and nested functions)
whose span is greater than `max_lines` (default 100). The span runs from the
`def` line (decorators are not counted) to the definition's last line,
inclusive.

```text
src/store.py:3-7 (Store.load): warning PYTHON_FUNCTION_TOO_LARGE: Function Store.load spans 5 lines; configured maximum is 4.
  evidence: actual_lines=5, maximum_lines=4, symbol="Store.load"
```

#### `PYTHON_CLASS_TOO_LARGE` (warning)

Reports a `class` (including nested classes) whose span is greater than
`max_lines` (default 500), with the same span semantics.

#### `PYTHON_BARE_EXCEPT` (warning)

Reports each syntactic bare `except:` clause. `except Exception:`, tuples,
named exceptions, and `except*` are not reported.

```text
src/store.py:6 (Store.load): warning PYTHON_BARE_EXCEPT: Bare 'except:' clause.
  evidence: symbol="Store.load"
```

#### `PYTHON_MUTABLE_DEFAULT` (warning)

Reports `def`/`async def` parameters (positional or keyword-only) whose
default is a list, dict, or set literal, or a list, dict, or set
comprehension. Calls such as `list()`, `dict()`, `set()`, or
`defaultdict(list)` are deliberately not reported, because the called name
may be shadowed. Lambdas are not checked.

```text
src/store.py:3 (Store.load): warning PYTHON_MUTABLE_DEFAULT: Parameter 'keys' of Store.load has a mutable default (list).
  evidence: default_kind="list", parameter="keys", parameter_kind="positional", symbol="Store.load"
```

### Swift rules

The Swift rules check source and test files ending in `.swift` with a
conservative, lightweight lexical scanner (not a parser or compiler). Each
file is scanned at most once per run, and code is never compiled or run.

The scanner ignores everything inside:

- `//` comments and `/* ... */` comments, including nested block comments;
- string literals: `"..."` with escapes, multiline `"""..."""`, and raw
  strings with any number of `#` (`#"..."#`, `##"""..."""##`);
- string interpolation `\( ... )`, which is skipped together with its
  string, so code inside an interpolation is never reported;
- extended regex literals `#/ ... /#`.

`try!` and `as!` are recognized only as Swift lexes them: the `!` must
directly follow the keyword. `try !x` (try applied to `!x`) and `x as !T`
are not forced operators; `try!=` and `as!!` contain the longer operators
`!=` and `!!`; and `retry!`, `x.try!`, and `` `try`! `` are not the keywords.
A keyword directly touching any non-ASCII character (for example `😀try!`)
is never reported, because Swift identifiers may contain such characters.

Bare regex literals (`/ ... /`) are not parsed. Instead, for a `/` with
another `/` later on the same line:

- If the `/` starts an expression (it follows `(`, `[`, `{`, `,`, `:`, `=`,
  `;`, or the start of the file) and is not followed by a space, it cannot be
  division, so the text up to the closing `/` is skipped as a regex.
- Otherwise it may be division or a regex, and `try!`/`as!` between the two
  slashes is not reported. If that text contains `"`, `#`, or a backtick,
  nothing is reported for the rest of the line.

Regex contents are therefore never reported; the cost is that some real uses
on a line with two slashes are missed (for example
`a / b; let c = try! d() / e`).

Known limitations:

- A bare regex spanning lines, or an ambiguous `/.../` region containing a
  multiline-string delimiter, can still confuse the scanner on later lines.
- Code in inactive `#if` branches is scanned like other code.
- An unterminated block comment or multiline string hides the rest of the
  file.
- Findings have line numbers but no columns; two uses on one line give two
  identical findings.

#### `SWIFT_FORCE_TRY` (warning)

Reports each `try!`. `try` and `try?` are not reported.

```text
Sources/App/Loader.swift:3: warning SWIFT_FORCE_TRY: Force-try operator 'try!' is used.
  evidence: operator="try!"
```

#### `SWIFT_FORCE_CAST` (warning)

Reports each `as!`. `as` and `as?` are not reported.

```text
Sources/App/Loader.swift:5: warning SWIFT_FORCE_CAST: Forced cast operator 'as!' is used.
  evidence: operator="as!"
```

### `TRACKED_BUILD_ARTIFACT` (warning)

Reports each file that is tracked by Git and classified as an obvious build
artifact:

- files under a top-level `build/` or `dist/` directory, that is, one that is
  the first directory relative to the analyzed path. Nested directories with
  these names (for example `docs/manuals/build/` or `src/mypkg/dist/`) are
  often source or documentation and are not treated as build output. When
  you analyze `packages/foo` directly, `packages/foo/build/` is its top-level
  `build/`;
- files under `.build/` (SwiftPM) or `*.egg-info` directories, at any depth;
- compiled outputs at any depth (`.o`, `.obj`, `.pyc`, `.pyo`, `.class`,
  `.so`, `.dylib`, `.dll`, `.a`, `.lib`, `.exe`).

Directory names are matched case-insensitively, relative to the analyzed
directory. Tracked files in directories that discovery skips, such as
`.build/` or `__pycache__/`, are still reported. Nothing is reported outside
a Git worktree.

The rule reports what is tracked, not what is ignored:

- Untracked files are never reported, whether or not `.gitignore` matches them.
- `.gitignore` does not suppress a file that is already tracked; Git keeps
  tracking it, so it is still reported.
- `repo_check.exclude` is how to suppress an artifact tracked on purpose.

```text
build/output.o: warning TRACKED_BUILD_ARTIFACT: Build artifact is tracked by Git.
  evidence: classification="build_artifact", tracked=true
```

The finding states a fact; some artifacts are committed on purpose. Add such
paths to `repo_check.exclude`, or disable the rule entirely:

```toml
[repo_check.rules.TRACKED_BUILD_ARTIFACT]
enabled = false
```

### History rules (`--history` only)

#### `FREQUENTLY_CHANGED_FILE` (info)

Reports each current source file that was touched by many of the scanned
commits. "Touched" means the commit added, modified, deleted, or changed the
type of the file (see [History](#history---history-n)); lines changed are not
counted. A file is reported when all of these hold:

- at least `min_scanned_commits` commits were scanned (default 50);
- at least `min_touching_commits` of them touched the file (default 10);
- they are at least `min_touch_percent` percent of the scanned commits
  (default 10), compared exactly with integers.

```text
src/app.py: info FREQUENTLY_CHANGED_FILE: Source file was touched in 17 of 100 scanned commits.
  evidence: history_head="<commit>", minimum_scanned_commits=50, minimum_touch_percent=10, minimum_touching_commits=10, requested_commits=100, scanned_commits=100, shallow=false, touching_commits=17
```

- Only files that currently exist and are classified as source are
  candidates. Tests, documentation, configuration, generated files, build
  artifacts, binaries, and unclassified files are not; neither are paths that
  exist only in history (deleted or renamed away). Classification is the same
  as elsewhere, so some files (for example `Package.swift`) count as source;
  use `repo_check.exclude` to leave a file out.
- The denominator is every scanned commit. The history window covers the
  whole repository, so when `PATH` is a subdirectory, commits that touched
  nothing beneath it still count.
- Results depend on the requested depth `N`: frequently changed files differ
  between short and long windows.
- In a shallow clone, only locally available commits are scanned, and
  `shallow` is `true` in the evidence. Older touches may be missing, so files
  can go unreported, but a reported count is always from real commits. A
  checkout with fewer than `min_scanned_commits` commits (such as a CI checkout
  with `fetch-depth: 1`) reports nothing.
- The finding is informational: it states where changes concentrated in the
  scanned history, not that a file is wrong. It does not affect the exit
  status unless `--fail-on info` is used.

### Change rules (`--compare` only)

#### `LARGE_CHANGESET` (warning)

Reports once when the number of changed files is greater than `max_files`
(default 50). Only the number of files counts, not lines changed.

```text
<repository>: warning LARGE_CHANGESET: Changeset contains 77 files; configured maximum is 50.
  evidence: changed_files=77, maximum_files=50
```

#### `PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE` (warning)

Reports once when at least one changed file is production source and none is
a test file. A renamed file counts by both its old and new path, so moving a
file between `src/` and `tests/` counts as both. Documentation,
configuration, generated, build-artifact, and binary files are neither.

```text
<repository>: warning PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE: Production source files changed without any test file changes.
  evidence: production_files=2, test_files=0
```

#### `SENSITIVE_PROJECT_FILE_CHANGED` (warning)

Reports each changed file from this fixed list (paths relative to `PATH`,
matched exactly and case-sensitively):

| Category | Files |
| --- | --- |
| `python_packaging` | `pyproject.toml`, `setup.py`, `setup.cfg` at the top level |
| `swift_package` | `Package.swift`, `Package.resolved` at the top level |
| `xcode_project` | `project.pbxproj` directly inside any `*.xcodeproj` directory |
| `github_workflow` | `*.yml` / `*.yaml` directly inside `.github/workflows/` |

Additions, modifications, deletions, and renames into or away from a listed
path all count; a rename produces one finding. The finding is reported at the
file's current path, and `matched_path` names the listed path.

```text
lib/core.py: warning SENSITIVE_PROJECT_FILE_CHANGED: Sensitive project file changed.
  evidence: category="python_packaging", matched_path="setup.py", old_path="setup.py", status="renamed"
```

## Running tests

```bash
python -m unittest discover -s tests
```

If the package is not installed, prefix the command with `PYTHONPATH=src`.
