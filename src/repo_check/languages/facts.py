"""Per-analysis cache of language facts, shared by all rules in one run.

The engine creates one ``AnalysisFacts`` per ``run_rules`` call and passes it
to every rule through ``RuleContext``. Facts are computed on first request
and memoized, so each file is read and analyzed at most once per analysis,
however many rules consume it, and not at all if no rule asks.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from repo_check.discovery import read_text_lines
from repo_check.languages import adapter_for
from repo_check.languages.base import LanguageAdapter
from repo_check.languages.python import PYTHON, PythonModuleFacts
from repo_check.languages.swift import SWIFT, SwiftFileFacts
from repo_check.model import DiscoveredFile

_Facts = TypeVar("_Facts")


class AnalysisFacts:
    def __init__(self) -> None:
        self._python: dict[str, PythonModuleFacts | None] = {}
        self._swift: dict[str, SwiftFileFacts | None] = {}

    def python(self, file: DiscoveredFile) -> PythonModuleFacts | None:
        """Python facts for a ``.py``/``.pyi`` file, or None if the file is not
        Python, is binary, or does not parse."""
        return _memoized(self._python, file, PYTHON, PYTHON.analyze)

    def swift(self, file: DiscoveredFile) -> SwiftFileFacts | None:
        """Swift facts for a ``.swift`` file, or None if the file is not Swift,
        is binary, or cannot be scanned."""
        return _memoized(self._swift, file, SWIFT, SWIFT.analyze)


def _memoized(
    cache: dict[str, _Facts | None],
    file: DiscoveredFile,
    adapter: LanguageAdapter,
    analyze: Callable[[Sequence[str]], _Facts | None],
) -> _Facts | None:
    path = file.relative_path
    if path not in cache:
        facts = None
        if adapter_for(path) is adapter:
            lines = read_text_lines(file)
            if lines is not None:
                facts = analyze(lines)
        cache[path] = facts
    return cache[path]
