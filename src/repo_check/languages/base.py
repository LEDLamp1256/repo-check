"""Internal seam for future language-specific analyzers.

An adapter declares the file extensions it handles. Each adapter exposes its
own typed analysis entry point (for example ``PythonAdapter.analyze``); there
is deliberately no generic analysis method. This is an internal interface,
not a public plugin API.
"""

from __future__ import annotations

from typing import Protocol


class LanguageAdapter(Protocol):
    name: str
    extensions: frozenset[str]
