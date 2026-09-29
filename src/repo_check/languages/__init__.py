"""Internal language adapter registry.

Adapters are internal descriptors (name and file extensions); this is not a
public plugin API. Language-specific facts are obtained through
``repo_check.languages.facts.AnalysisFacts``.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from repo_check.languages.base import LanguageAdapter
from repo_check.languages.python import PYTHON
from repo_check.languages.swift import SWIFT

ADAPTERS: tuple[LanguageAdapter, ...] = (PYTHON, SWIFT)


def adapter_for(relative_path: str) -> LanguageAdapter | None:
    suffix = PurePosixPath(relative_path).suffix.lower()
    for adapter in ADAPTERS:
        if suffix in adapter.extensions:
            return adapter
    return None


__all__ = ["ADAPTERS", "LanguageAdapter", "adapter_for"]
