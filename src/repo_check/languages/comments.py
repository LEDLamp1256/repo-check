"""Internal, language-aware extraction of comment markers.

Given a relative path (whose extension selects the comment syntax) and the
file's decoded lines, ``extract_comment_markers`` yields ``CommentMarker``
facts for words such as ``TODO`` that begin a comment. It knows nothing about
rules, severities, or findings.

Extraction is deliberately conservative (a missed marker is preferable to a
false positive):

* Known syntaxes only: ``#`` comments, or ``//`` and ``/* ... */`` comments,
  chosen by file extension. Other files yield nothing.
* A marker must be the exact (case-sensitive) word and the first word of the
  comment (``# TODO: ...``, ``// FIXME(name) ...``, or a line inside a block
  comment such as `` * TODO ...``). Prose mentions ("see the TODO list") and
  identifiers (``TODO_ITEMS``) do not match.
* A comment token must start the line or follow whitespace, and must not sit
  inside a single-line string literal (by quote parity).
* Lines that start inside, or contain the delimiter of, a multi-line string
  (Python/Swift/Kotlin triple quotes, JavaScript/Go backticks) are skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath


@dataclass(frozen=True)
class CommentMarker:
    """A marker word that begins a comment.

    ``text`` is the comment from the marker onward, with surrounding
    whitespace and a closing ``*/`` removed.
    """

    line_number: int
    marker: str
    text: str


@dataclass(frozen=True)
class _Syntax:
    line_tokens: tuple[str, ...]
    block: bool
    quotes: str
    multiline_delimiters: tuple[str, ...]


_HASH = _Syntax(("#",), block=False, quotes="\"'", multiline_delimiters=())
_PYTHON = _Syntax(("#",), block=False, quotes="\"'", multiline_delimiters=('"""', "'''"))
_C_LIKE = _Syntax(("//",), block=True, quotes='"', multiline_delimiters=())
_TRIPLE_QUOTE_C_LIKE = _Syntax(("//",), block=True, quotes='"', multiline_delimiters=('"""',))
_JS_LIKE = _Syntax(("//",), block=True, quotes="\"'", multiline_delimiters=("`",))
_GO = _Syntax(("//",), block=True, quotes='"', multiline_delimiters=("`",))
_DART = _Syntax(("//",), block=True, quotes="\"'", multiline_delimiters=('"""', "'''"))

_SYNTAX_BY_EXTENSION: dict[str, _Syntax] = {
    **dict.fromkeys((".py", ".pyi"), _PYTHON),
    **dict.fromkeys((".sh", ".bash", ".zsh", ".rb", ".pl", ".ex", ".exs"), _HASH),
    **dict.fromkeys(
        (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm", ".cs", ".rs"), _C_LIKE
    ),
    **dict.fromkeys((".swift", ".kt", ".kts", ".scala", ".java"), _TRIPLE_QUOTE_C_LIKE),
    **dict.fromkeys((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"), _JS_LIKE),
    ".go": _GO,
    ".dart": _DART,
}


def has_comment_syntax(relative_path: str) -> bool:
    """True if markers can be extracted from files with this path's extension."""
    return _syntax_for(relative_path) is not None


def extract_comment_markers(
    relative_path: str, lines: Sequence[str], markers: Iterable[str]
) -> Iterator[CommentMarker]:
    """Yield markers (from ``markers``) that begin a comment, in line order."""
    syntax = _syntax_for(relative_path)
    if syntax is None:
        return
    pattern = _marker_pattern(tuple(sorted(markers)))
    for number, match in _scan(lines, syntax, pattern):
        yield CommentMarker(line_number=number, marker=match.group(2), text=_clean(match.group(1)))


def _syntax_for(relative_path: str) -> _Syntax | None:
    return _SYNTAX_BY_EXTENSION.get(PurePosixPath(relative_path).suffix.lower())


@lru_cache(maxsize=None)
def _marker_pattern(markers: tuple[str, ...]) -> re.Pattern[str]:
    # A marker as the first word of the comment body; group 1 is the comment
    # text from the marker on, group 2 the marker itself.
    if not markers or not all(re.fullmatch(r"\w+", marker) for marker in markers):
        raise ValueError(f"markers must be non-empty words, got {markers!r}")
    return re.compile(r"\s*((" + "|".join(markers) + r")(?!\w).*)")


def _scan(
    lines: Sequence[str], syntax: _Syntax, pattern: re.Pattern[str]
) -> Iterator[tuple[int, re.Match[str]]]:
    open_string: str | None = None
    in_block = False
    for number, line in enumerate(lines, start=1):
        if open_string is not None or any(d in line for d in syntax.multiline_delimiters):
            open_string = _update_open_string(open_string, line, syntax)
            continue

        if in_block:
            end = line.find("*/")
            match = pattern.match(line[:end] if end >= 0 else line, _block_body_start(line))
            if match:
                yield number, match
            if end >= 0:
                in_block = False
            continue

        found = _first_comment(line, syntax)
        if found is None:
            continue
        token, position = found
        body_start = _skip_token(line, token, position)
        body = line[body_start:]
        if token == "/*":
            end = body.find("*/")
            if end < 0:
                in_block = True
            else:
                body = body[:end]
        match = pattern.match(body)
        if match:
            yield number, match


def _first_comment(line: str, syntax: _Syntax) -> tuple[str, int] | None:
    tokens = syntax.line_tokens + (("/*",) if syntax.block else ())
    best: tuple[str, int] | None = None
    outside: list[bool] | None = None  # computed once per line, when first needed
    for token in tokens:
        start = 0
        while (position := line.find(token, start)) >= 0:
            if position == 0 or line[position - 1].isspace():
                if outside is None:
                    outside = _outside_strings(line, syntax.quotes)
                if outside[position]:
                    if best is None or position < best[1]:
                        best = (token, position)
                    break
            start = position + 1
    return best


def _outside_strings(line: str, quotes: str) -> list[bool]:
    """``result[i]`` is True when ``line[:i]``, with backslash escapes removed,
    has an even number of each quote character (``i`` is not inside a
    single-line string by quote parity). One pass, so a line with many
    comment-like tokens inside strings stays linear.

    Only positions after whitespace (or 0) are queried; such a position never
    splits an escape pair, so pairing escapes over the whole line gives the
    same answer as pairing them over each prefix.
    """
    result = [True] * (len(line) + 1)
    odd = {quote: False for quote in quotes}
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            result[index + 1] = result[index + 2] = not any(odd.values())
            index += 2
            continue
        if char in odd:
            odd[char] = not odd[char]
        index += 1
        result[index] = not any(odd.values())
    return result


def _skip_token(line: str, token: str, position: int) -> int:
    index = position + len(token)
    repeat = token[-1]  # "##", "///", "/**" are the same comment kinds
    while index < len(line) and line[index] == repeat:
        index += 1
    return index


def _block_body_start(line: str) -> int:
    index = len(line) - len(line.lstrip())
    while index < len(line) and line[index] == "*" and not line.startswith("*/", index):
        index += 1
    return index


def _update_open_string(open_string: str | None, line: str, syntax: _Syntax) -> str | None:
    index = 0
    while True:
        if open_string is None:
            hits = [(line.find(d, index), d) for d in syntax.multiline_delimiters]
            hits = [(pos, d) for pos, d in hits if pos >= 0]
            if not hits:
                return None
            position, open_string = min(hits)
            index = position + len(open_string)
        else:
            position = line.find(open_string, index)
            if position < 0:
                return open_string
            index = position + len(open_string)
            open_string = None


def _clean(text: str) -> str:
    text = text.strip()
    if text.endswith("*/"):
        text = text[:-2].rstrip()
    return text
