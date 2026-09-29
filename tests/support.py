"""Shared helpers for the RepoCheck test suite."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import repo_check

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SRC_DIR = Path(repo_check.__file__).resolve().parent.parent


def numbered_lines(count: int) -> str:
    """Return ``count`` newline-terminated lines of trivial Python."""
    return "".join(f"value_{i} = {i}\n" for i in range(1, count + 1))


def write_tree(root: Path, files: dict[str, str | bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")


def run_cli(
    *args: str, cwd: Path | None = None, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m repo_check`` in a subprocess with the package importable."""
    env = dict(os.environ)
    env.update(env_overrides or {})
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC_DIR), env.get("PYTHONPATH")]))
    return subprocess.run(
        [sys.executable, "-m", "repo_check", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env=env,
        check=False,
    )


GIT = shutil.which("git")
requires_git = unittest.skipUnless(GIT, "git executable not available")


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` isolated from user/system config; return stdout."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            # Fixed commit metadata keeps fixture commits reproducible.
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
        }
    )
    completed = subprocess.run(
        [
            GIT or "git",
            "-c", "user.name=RepoCheck Tests",
            "-c", "user.email=tests@example.invalid",
            "-c", "init.defaultBranch=main",
            "-C", str(repo),
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return completed.stdout


def make_git_repo(
    root: Path,
    tracked: dict[str, str | bytes],
    untracked: dict[str, str | bytes] | None = None,
) -> Path:
    """Create a Git repository at ``root``, commit ``tracked``, then write
    ``untracked`` files without adding them. Returns the resolved root."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    write_tree(root, tracked)
    if tracked:
        # -f: tracked fixtures must be added even if an ambient ignore rule
        # (e.g. a user-level excludes file) would ignore build outputs.
        git(root, "add", "-f", "--", *tracked)
        git(root, "commit", "-q", "-m", "fixture")
    write_tree(root, untracked or {})
    return root.resolve()


def commit_all(repo: Path, message: str = "change") -> str:
    """Stage everything (including ignored files) and commit; return the SHA."""
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def make_branch_repo(
    root: Path,
    base: dict[str, str | bytes],
    branch: str = "feature",
) -> Path:
    """A repository with ``base`` committed on ``main`` and a checked-out
    ``branch`` created from it. Returns the resolved root."""
    repo = make_git_repo(root, base)
    if not base:
        commit_all(repo, "base")
    git(repo, "checkout", "-q", "-b", branch)
    return repo
