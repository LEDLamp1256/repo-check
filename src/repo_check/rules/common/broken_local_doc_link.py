"""BROKEN_LOCAL_DOC_LINK: relative Markdown links to paths that do not exist.

Existence is decided only from snapshot facts (``RepositorySnapshot.path_kind``):
a link is reported only when its target is MISSING. Targets that exist as
files or directories, or that fall inside something discovery did not index
(pruned or excluded paths, symlinks), are never reported.

Documentation-site links are accepted: an extensionless target such as
``npm-config`` or an ``.html`` target such as ``guide.html`` is treated as
existing when the corresponding ``.md``/``.markdown`` file exists, because
static site generators resolve such links to the Markdown page.

Checked: inline links and images ``[text](target)`` / ``![alt](target)`` and
reference definitions ``[id]: target`` in Markdown documentation files, except
documents inside a DocC catalog (a ``*.docc`` directory), where DocC rather
than the file system resolves links.

Not checked (conservatively skipped): URLs with a scheme (``https:``,
``mailto:``), protocol-relative ``//host`` links, root-absolute ``/path``
links, same-page ``#anchor`` links, templated targets (``{``, ``}``, ``$``),
targets containing backslashes, bare e-mail addresses written without
``mailto:`` (``name@example.com``), and targets that resolve above the
analysis root. Fragments and query strings are ignored (anchors are not validated).
Fenced code blocks, inline code spans, and HTML comments are ignored, as are
lines that may be indented code (4+ leading spaces or a leading tab). The
latter also skips deeply indented list content; precision is preferred over
recovering links from ambiguous indentation.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import unquote

from repo_check.discovery import read_text_lines
from repo_check.model import (
    FileClassification,
    Finding,
    Location,
    PathKind,
    RepositorySnapshot,
    RuleMetadata,
    Severity,
)
from repo_check.rules.base import RuleContext

MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown"})
DOCC_CATALOG_SUFFIX = ".docc"

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
_INLINE_LINK = re.compile(
    r"(?<![\\\w\]])!?\["
    r"((?:[^\[\]\n]|\[[^\[\]\n]*\])*)"  # link text, one level of nested brackets
    r"\]\(\s*"
    r"(<[^<>\n]*>|[^\s()<>]*(?:\([^\s()]*\)[^\s()<>]*)*)"  # destination
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?"  # optional title
    r"\s*\)"
)
_REFERENCE_DEFINITION = re.compile(r"^ {0,3}\[(?!\^)[^\]\n]+\]:\s*(<[^<>\n]*>|\S+)")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


class BrokenLocalDocLink:
    metadata = RuleMetadata(
        id="BROKEN_LOCAL_DOC_LINK",
        title="Broken local documentation link",
        category="documentation",
        default_severity=Severity.WARNING,
        description=(
            "Reports relative links in Markdown documentation whose target file or "
            "directory does not exist in the analyzed tree."
        ),
        remediation=(
            "Fix the link path, restore the missing file, or remove the link. If the "
            "target is created at build time, exclude the document via repo_check.exclude."
        ),
    )

    def check_repository(
        self, snapshot: RepositorySnapshot, context: RuleContext
    ) -> Iterator[Finding]:
        case_index: dict[str, str] | None = None
        for file in snapshot.files:
            if file.classification is not FileClassification.DOCUMENTATION:
                continue
            if PurePosixPath(file.relative_path).suffix.lower() not in MARKDOWN_EXTENSIONS:
                continue
            if _in_docc_catalog(file.relative_path):
                continue
            lines = read_text_lines(file)
            if lines is None:
                continue
            base = posixpath.dirname(file.relative_path)
            reported: set[tuple[int, str]] = set()
            for line_number, target in extract_link_targets(lines):
                resolved = resolve_target(base, target)
                if resolved is None or not _is_missing(snapshot, resolved):
                    continue
                if (line_number, target) in reported:
                    continue
                reported.add((line_number, target))
                evidence: dict[str, str] = {"link": target, "resolved_path": resolved}
                if case_index is None:
                    case_index = _case_index(snapshot)
                case_match = case_index.get(resolved.casefold())
                if case_match is not None:
                    evidence["case_insensitive_match"] = case_match
                yield Finding(
                    rule_id=self.metadata.id,
                    severity=self.metadata.default_severity,
                    message=f"Local link target does not exist: {target}",
                    path=file.relative_path,
                    location=Location(start_line=line_number),
                    evidence=evidence,
                )


def _in_docc_catalog(relative_path: str) -> bool:
    """True for a document inside a DocC catalog (a ``*.docc`` directory).

    DocC does not resolve Markdown links against the file system: images are
    found by file name anywhere in the catalog, and relative links become
    hosted documentation URLs. Checking them as file paths would report
    working links as broken.
    """
    return any(
        part.lower().endswith(DOCC_CATALOG_SUFFIX)
        for part in PurePosixPath(relative_path).parts[:-1]
    )


def extract_link_targets(lines: tuple[str, ...]) -> Iterator[tuple[int, str]]:
    """Yield (line number, raw link destination) outside code and comments."""
    fence: str | None = None
    in_comment = False
    for number, line in enumerate(lines, start=1):
        fence_match = _FENCE.match(line)
        if fence is not None:
            if fence_match and _closes_fence(fence, fence_match):
                fence = None
            continue
        if fence_match and not (fence_match.group(1)[0] == "`" and "`" in fence_match.group(2)):
            fence = fence_match.group(1)
            continue

        text, in_comment = _strip_html_comments(line, in_comment)
        if line.startswith(("    ", "\t")):
            continue  # possible indented code block
        text = _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), text)

        definition = _REFERENCE_DEFINITION.match(text)
        if definition:
            yield number, _unwrap(definition.group(1))
            continue
        yield from ((number, target) for target in _inline_targets(text))


def resolve_target(base: str, target: str) -> str | None:
    """Resolve a link destination to a normalized path relative to the
    analysis root, or None if it is not a checkable local link."""
    if not target or target.startswith(("#", "/")) or _SCHEME.match(target):
        return None
    if any(char in target for char in "{}$\\"):
        return None
    if "@" in target and "/" not in target:
        return None  # an e-mail address missing "mailto:", not a file reference
    path = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not path:
        return None
    resolved = posixpath.normpath(posixpath.join(base, path))
    if resolved == "..":
        return None
    if resolved.startswith("../"):
        return None
    return "" if resolved == "." else resolved


def _is_missing(snapshot: RepositorySnapshot, resolved: str) -> bool:
    return all(
        snapshot.path_kind(candidate) is PathKind.MISSING
        for candidate in _site_candidates(resolved)
    )


def _site_candidates(resolved: str) -> tuple[str, ...]:
    """The path itself, plus the Markdown sources a docs site would serve it from."""
    path = PurePosixPath(resolved)
    if not resolved or path.suffix == "":
        stem = resolved
    elif path.suffix.lower() in (".html", ".htm"):
        stem = resolved[: -len(path.suffix)]
    else:
        return (resolved,)
    return (resolved, *(stem + ext for ext in sorted(MARKDOWN_EXTENSIONS)))


def _inline_targets(text: str) -> Iterator[str]:
    for match in _INLINE_LINK.finditer(text):
        # Link text may itself hold a link or image, e.g. a badge:
        # [![alt](badge.svg)](target)
        yield from _inline_targets(match.group(1))
        yield _unwrap(match.group(2))


def _unwrap(destination: str) -> str:
    if destination.startswith("<") and destination.endswith(">"):
        return destination[1:-1].strip()
    return destination


def _closes_fence(fence: str, match: re.Match[str]) -> bool:
    marker = match.group(1)
    return marker[0] == fence[0] and len(marker) >= len(fence) and not match.group(2).strip()


def _strip_html_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    if in_comment:
        end = line.find("-->")
        if end < 0:
            return "", True
        line = line[end + 3 :]
    # Replace each complete comment with a space, left to right; an unclosed
    # one hides the rest of the line. Linear even with many unclosed "<!--".
    parts = []
    index = 0
    while (start := line.find("<!--", index)) >= 0:
        parts.append(line[index:start])
        end = line.find("-->", start + 4)
        if end < 0:
            return "".join(parts), True
        parts.append(" ")
        index = end + 3
    parts.append(line[index:])
    return "".join(parts), False


def _case_index(snapshot: RepositorySnapshot) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in sorted([f.relative_path for f in snapshot.files] + list(snapshot.directories)):
        index.setdefault(path.casefold(), path)
    return index
