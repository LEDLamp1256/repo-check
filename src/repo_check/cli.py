"""Command-line interface.

Exit statuses:
    0  analysis succeeded and no finding met the --fail-on threshold
    1  at least one finding met the --fail-on threshold
    2  execution or configuration error (including invalid arguments and
       a --compare or --history request that cannot be carried out)

``--fail-on`` defaults to ``error``: warning and info findings are reported
but do not change the exit status unless requested.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

from repo_check import __version__
from repo_check.engine import analyze
from repo_check.errors import RepoCheckError
from repo_check.model import Severity
from repo_check.output import render_json, render_text

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

DEFAULT_FAIL_ON = Severity.ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-check",
        description="Deterministic code and repository health analyzer.",
    )
    parser.add_argument("path", type=Path, help="repository directory to analyze")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help="configuration file (default: PATH/repo-check.toml if present)",
    )
    parser.add_argument(
        "--fail-on",
        choices=[severity.value for severity in Severity],
        default=DEFAULT_FAIL_ON.value,
        metavar="LEVEL",
        help=(
            "exit with status 1 if any finding has at least this severity: "
            f"info, warning, or error (default: {DEFAULT_FAIL_ON.value})"
        ),
    )
    parser.add_argument(
        "--compare",
        metavar="REF",
        default=None,
        help=(
            "also analyze the committed changes on HEAD since its merge base with REF "
            "(a local branch, tag, or commit; nothing is fetched)"
        ),
    )
    parser.add_argument(
        "--history",
        metavar="N",
        type=_positive_int,
        default=None,
        help=(
            "also collect at most N non-merge commits reachable from HEAD for "
            "history analysis (local history only; nothing is fetched)"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = analyze(
            args.path, args.config, compare_ref=args.compare, history_commits=args.history
        )
        render = render_json if args.format == "json" else render_text
        _write_stdout(render(result.findings))
    except RepoCheckError as exc:
        print(f"repo-check: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception:
        # Keep the exit-status contract: an unexpected failure must never look
        # like "findings met the threshold" (status 1).
        traceback.print_exc()
        print("repo-check: internal error", file=sys.stderr)
        return EXIT_ERROR

    threshold = Severity(args.fail_on)
    if any(finding.severity.meets(threshold) for finding in result.findings):
        return EXIT_FINDINGS
    return EXIT_OK


def _positive_int(text: str) -> int:
    """argparse type for --history: a decimal integer of at least 1."""
    if not text.isascii() or not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}")
    return int(text)


def _write_stdout(text: str) -> None:
    # Characters the output stream cannot encode (for example a non-ASCII
    # file name on a cp1252 console) are written as backslash escapes rather
    # than failing the run.
    encoding = getattr(sys.stdout, "encoding", None)
    if encoding:
        text = text.encode(encoding, "backslashreplace").decode(encoding)
    sys.stdout.write(text)


def run() -> None:
    """Console-script entry point."""
    sys.exit(main())
