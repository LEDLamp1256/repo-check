# Releasing RepoCheck

A manual checklist for the repository owner. Nothing here is automated; each
public step is an explicit owner decision.

1. Merge the release-preparation feature PR into `dev`.
2. Finalize the changelog. `0.1.0` must not ship marked `Unreleased`.
   - Choose the actual release date.
   - In `CHANGELOG.md`, change `## 0.1.0 - Unreleased` to
     `## 0.1.0 - YYYY-MM-DD`, using that date.
   - Commit this change on `dev`. The resulting commit is the release
     candidate; every later step uses this exact commit.
3. Confirm CI is green on that exact `dev` commit.
4. Confirm the version is `0.1.0` in `src/repo_check/__init__.py`, and that
   `CHANGELOG.md` describes this release under the dated heading.
5. Run the tests on a clean checkout of that `dev` commit:

   ```bash
   PYTHONPATH=src python -m unittest discover -s tests
   ```

6. Build a clean wheel and sdist from that checkout, outside any existing
   `dist/`:

   ```bash
   python -m venv /tmp/rc-build
   /tmp/rc-build/bin/python -m pip install build twine
   /tmp/rc-build/bin/python -m build --outdir /tmp/rc-dist .
   /tmp/rc-build/bin/python -m twine check --strict /tmp/rc-dist/*
   ```

7. Fresh-install acceptance. In a new virtual environment outside the
   checkout, with no `PYTHONPATH`, install the wheel and, from an unrelated
   directory, check the following:
   - `repo-check --version` and `python -m repo_check --version` print
     `repo-check 0.1.0`.
   - A small repository analyzes cleanly, with exit 0.
   - `--fail-on warning` gives exit 1 on a warning.
   - An invalid path gives exit 2.
   - `--format json` reports `schema_version` 1.
   - `--compare` works against a local ref.

   Repeat the install from the sdist.
8. Inspect the artifacts:
   - The metadata shows name `repo-check`, version `0.1.0`,
     `Requires-Python >=3.11`, no `Requires-Dist`, and the `repo-check`
     console script.
   - The wheel contains only the `repo_check` package.
   - The sdist contains `README.md` and the dated `CHANGELOG.md`.
   - Neither artifact contains caches, virtual environments, or local paths.
9. Open a pull request from that exact `dev` commit to `main` and get it
   reviewed.
10. The owner approves the merge into `main`.
11. Merge exactly the reviewed release candidate into `main`.
12. Create the tag `v0.1.0` on that exact `main` release commit:

    ```bash
    git fetch origin
    git tag -a v0.1.0 -m "RepoCheck 0.1.0" <main release commit SHA>
    git push origin v0.1.0
    ```

13. Verify the tag points to that commit:
    `git rev-parse v0.1.0^{commit}` must equal the `main` release commit SHA.
14. A GitHub Release and PyPI publication are optional. Each is a separate,
    explicit owner decision and is not part of this checklist.
