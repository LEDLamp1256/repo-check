# Releasing RepoCheck

A manual checklist for maintainers, written for the `0.1.0` release. Nothing
here is automated; each public step is an explicit owner decision. For a
later release, substitute its version for `0.1.0` throughout.

1. The release candidate is prepared on `dev`:
   - `__version__` in `src/repo_check/__init__.py` is `0.1.0`.
   - `CHANGELOG.md` describes the release under `## 0.1.0 - YYYY-MM-DD`,
     with the actual release date. A release must not ship marked
     `Unreleased`.
   - Version references in `README.md` and the tests match.

   The resulting `dev` commit is the release candidate; every later step
   uses this exact commit.
2. Confirm CI is green on that exact `dev` commit.
3. Run the tests on a clean checkout of that commit:

   ```bash
   PYTHONPATH=src python -m unittest discover -s tests
   ```

4. Build a clean wheel and sdist from that checkout, outside any existing
   `dist/`:

   ```bash
   python -m venv /tmp/rc-build
   /tmp/rc-build/bin/python -m pip install build twine
   /tmp/rc-build/bin/python -m build --outdir /tmp/rc-dist .
   /tmp/rc-build/bin/python -m twine check --strict /tmp/rc-dist/*
   ```

5. Fresh-install acceptance. In a new virtual environment outside the
   checkout, with no `PYTHONPATH`, install the wheel and, from an unrelated
   directory, check the following:
   - `repo-check --version` and `python -m repo_check --version` print
     `repo-check 0.1.0`.
   - A small repository analyzes cleanly, with exit 0.
   - `--fail-on warning` gives exit 1 on a warning.
   - An invalid path gives exit 2.
   - `--format json` reports `schema_version` 1.
   - `--compare` works against a local ref, and `--history` runs on a
     repository with commits.

   Repeat the install from the sdist.
6. Inspect the artifacts:
   - The metadata shows name `repo-check`, version `0.1.0`,
     `Requires-Python >=3.11`, `License-Expression: Apache-2.0`, no
     `Requires-Dist`, and the `repo-check` console script.
   - The wheel contains only the `repo_check` package and its metadata,
     including `LICENSE`.
   - The sdist contains `README.md`, `LICENSE`, and the dated `CHANGELOG.md`.
   - Neither artifact contains caches, virtual environments, or local paths.
7. Open a pull request from that exact `dev` commit to `main` and get it
   reviewed.
8. The owner approves the merge into `main`.
9. Merge exactly the reviewed release candidate into `main`.
10. Create the annotated tag `v0.1.0` on that exact `main` release commit:

    ```bash
    git fetch origin
    git tag -a v0.1.0 -m "RepoCheck 0.1.0" <main release commit SHA>
    git push origin v0.1.0
    ```

11. Verify the tag points to that commit:
    `git rev-parse v0.1.0^{commit}` must equal the `main` release commit SHA.
12. A GitHub Release and PyPI publication are optional. Each is a separate,
    explicit owner decision and is not part of this checklist.
