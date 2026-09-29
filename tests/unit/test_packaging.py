"""Release contract: version, entry points, and packaging metadata."""

import tomllib
import unittest
from pathlib import Path

import repo_check
from repo_check import cli

from support import run_cli

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


class PackagingTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(repo_check.__version__, "0.2.0.dev0")

    def test_version_flag_via_module(self):
        result = run_cli("--version")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "repo-check 0.2.0.dev0\n", ""))

    def test_pyproject_release_metadata(self):
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        project = data["project"]
        self.assertEqual(project["name"], "repo-check")
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["dependencies"], [])  # no runtime dependencies
        self.assertEqual(project["dynamic"], ["version"])
        self.assertEqual(project["scripts"], {"repo-check": "repo_check.cli:run"})
        self.assertEqual(
            data["tool"]["setuptools"]["dynamic"]["version"], {"attr": "repo_check.__version__"}
        )

    def test_console_script_target_exists(self):
        self.assertTrue(callable(cli.run))


if __name__ == "__main__":
    unittest.main()
