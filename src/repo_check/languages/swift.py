"""Internal, conservative Swift lexical extraction.

``analyze_swift`` scans a Swift file's decoded lines once and returns
``SwiftFileFacts``: the forced operators (``try!`` and ``as!``) that occur in
actual code. It is a lightweight lexer, not a parser: it never compiles or
executes code and has no notion of types or declarations.

What the lexer separates from code:

* ``//`` line comments and ``/* ... */`` block comments, including nested
  block comments.
* String literals: ``"..."`` (with ``\\`` escapes), multiline ``\"\"\"...\"\"\"``,
  and raw strings with one or more ``#`` delimiters (``#"..."#``,
  ``##\"\"\"...\"\"\"##``), whose escapes are ``\\#`` (with matching ``#``).
  Interpolations ``\\( ... )`` are skipped as part of their string: nested
  parentheses, strings, and comments inside them are tracked only to find the
  end of the string, and nothing inside a string is reported.
* Extended regex literals ``#/ ... /#`` (with matching ``#``).
* Backtick-escaped identifiers (`` `try` ``), which are ordinary names.

Operator recognition follows Swift's lexing rules for ``!``:

* ``try!`` / ``as!`` is reported only when ``!`` immediately follows the
  keyword (no whitespace or comment between): only a left-bound ``!`` is the
  postfix ``!`` of a forced operator. ``try !x`` is ``try`` applied to ``!x``
  and ``x as !T`` is not a forced cast, so neither is reported.
* Operator characters are lexed greedily, so ``try!=`` or ``as!!`` do not
  contain the ``!`` token and are not reported (``try!//comment`` and
  ``try!/*c*/`` are, because comments end an operator).
* The keyword must be a whole token and not a member name: ``retry!``,
  ``x.try!``, ``@as``, ``#as``, ``$as`` are not reported.
* Unicode: Swift identifiers may contain many non-ASCII characters (letters,
  combining marks, emoji). Rather than model that grammar, any non-ASCII
  character is treated as a possible identifier character, so a keyword
  directly touching one (``😀try!``, ``try😀!``, ``a\u0301try!``) is never
  reported.

Bare regex literals ``/ ... /`` are handled by a conservative ambiguity policy,
not a regex parser. At a code ``/`` (not a comment start) that has an
unescaped closing ``/`` later on the same line:

* If the ``/`` is in expression-start position (the previous code character
  is the start of the file or one of ``( [ { , : = ;``) and is not followed
  by a space or tab, it cannot be division, so the region is skipped as a
  regex literal and scanning continues after the closing ``/``.
* Otherwise it may be a regex or division. Scanning continues as division,
  but forced operators between the two slashes are suppressed; if the region
  contains a character that could change lexer state (``"``, ``#``, or a
  backtick), forced operators up to the end of the line are suppressed.

Known limitations (deliberate):

* Ambiguous slashes cause false negatives: ``a / b; let c = try! d() / e``
  reports nothing. Regex contents are never reported.
* A bare regex that spans lines, or an ambiguous region containing a
  multiline-string delimiter (three double quotes), can still desynchronize
  the scanner on later lines.
* Code inside string interpolation is never reported.
* An unterminated block comment or multiline string hides the rest of the
  file; an unterminated single-line string ends at its line.
* Code in inactive ``#if`` branches is scanned like any other code.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

FORCE_TRY = "try!"
FORCE_CAST = "as!"
_FORCED_KEYWORDS = {"try": FORCE_TRY, "as": FORCE_CAST}

# Characters that continue an operator token after "!" (Swift operator
# characters). "/" is special-cased: "//" and "/*" start comments.
_OPERATOR_CHARACTERS = frozenset("/=-+!*%<>&|^~?")
# A keyword preceded by one of these is not the keyword token.
_NON_KEYWORD_PREFIXES = frozenset(".@#$`\\")
# A "/" after one of these (or at the start of the file) is in expression-start
# position, where it cannot be division.
_REGEX_START_AFTER = frozenset("([{,:=;")
# Characters whose presence inside an ambiguous /.../ region could change
# lexer state (strings, raw strings/#-syntax, escaped identifiers).
_STATE_CHANGING = frozenset('"#`')


@dataclass(frozen=True)
class SwiftOperatorUse:
    """A forced operator in code. ``column`` is 1-based, at the keyword."""

    operator: str
    line: int
    column: int


@dataclass(frozen=True)
class SwiftFileFacts:
    operator_uses: tuple[SwiftOperatorUse, ...] = ()


@dataclass(frozen=True)
class SwiftAdapter:
    """Language adapter descriptor for Swift (see ``repo_check.languages``)."""

    name: str = "swift"
    extensions: frozenset[str] = field(default_factory=lambda: frozenset({".swift"}))

    def analyze(self, lines: Sequence[str]) -> SwiftFileFacts | None:
        return analyze_swift(lines)


SWIFT = SwiftAdapter()


def analyze_swift(lines: Sequence[str]) -> SwiftFileFacts | None:
    """Scan once and extract facts; None only for pathologically nested input."""
    text = "\n".join(lines)
    lexer = _Lexer(text)
    try:
        lexer.scan_code(0, record=True, until_close_paren=False)
    except RecursionError:
        return None
    return SwiftFileFacts(operator_uses=tuple(lexer.uses))


def _is_identifier_start(char: str) -> bool:
    # Any non-ASCII character may belong to a Swift identifier; treating it as
    # one keeps a keyword that touches it from being taken as the keyword.
    return char.isalpha() or char == "_" or not char.isascii()


def _is_identifier_char(char: str) -> bool:
    return char.isalnum() or char == "_" or not char.isascii()


class _Lexer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.length = len(text)
        self.uses: list[SwiftOperatorUse] = []
        # Last significant code character ("" at start of file, "a" for an
        # operand such as an identifier or literal); used for "/" disambiguation.
        self._last = ""
        # Forced operators starting before this index are suppressed.
        self._suppress_until = 0
        self._line_starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]

    def _position(self, index: int) -> tuple[int, int]:
        line = bisect_right(self._line_starts, index)
        return line, index - self._line_starts[line - 1] + 1

    def scan_code(self, i: int, *, record: bool, until_close_paren: bool) -> int:
        """Scan code from ``i``. In interpolation mode, stop after the ``)``
        that closes the interpolation and return the index after it."""
        text, length = self.text, self.length
        depth = 0
        while i < length:
            char = text[i]
            if char == "/" and text.startswith("//", i):
                end = text.find("\n", i)
                i = length if end < 0 else end
            elif char == "/" and text.startswith("/*", i):
                i = self._skip_block_comment(i)
            elif char == "/":
                i = self._slash(i)
            elif char == '"':
                i = self._skip_string(i, hashes=0)
                self._last = '"'
            elif char == "#":
                i = self._skip_hash_literal(i)
            elif char == "`":
                end = text.find("`", i + 1)
                newline = text.find("\n", i + 1)
                i = end + 1 if end >= 0 and (newline < 0 or end < newline) else i + 1
                self._last = "a"
            elif _is_identifier_start(char) or char == "$":
                start = i
                i += 1
                while i < length and _is_identifier_char(text[i]):
                    i += 1
                if record:
                    self._check_forced_operator(start, i)
                self._last = "a"
            elif char.isdigit():
                while i < length and _is_identifier_char(text[i]):
                    i += 1
                self._last = "a"
            elif until_close_paren and char == "(":
                depth += 1
                i += 1
                self._last = char
            elif until_close_paren and char == ")":
                self._last = char
                if depth == 0:
                    return i + 1
                depth -= 1
                i += 1
            else:
                if not char.isspace():
                    self._last = char
                i += 1
        return i

    def _slash(self, i: int) -> int:
        """At a "/" that does not start a comment: division or bare regex."""
        text = self.text
        close = self._regex_close(i)
        if close < 0:
            self._last = "/"
            return i + 1
        closes_cleanly = not text.startswith(("//", "/*"), close)
        if (
            closes_cleanly
            and (self._last == "" or self._last in _REGEX_START_AFTER)
            and text[i + 1] not in " \t"
        ):
            self._last = "a"  # a regex literal is an operand
            return close + 1
        # Ambiguous: continue as division, but never report forced operators
        # that could be regex contents.
        end = close + 1
        if any(char in _STATE_CHANGING for char in text[i + 1 : close]) or not closes_cleanly:
            newline = text.find("\n", i)
            end = self.length if newline < 0 else newline
        self._suppress_until = max(self._suppress_until, end)
        self._last = "/"
        return i + 1

    def _regex_close(self, i: int) -> int:
        """Index of the next unescaped "/" on the same line after ``i``, or -1."""
        j = i + 1
        while j < self.length and self.text[j] != "\n":
            if self.text[j] == "\\":
                j += 2
                continue
            if self.text[j] == "/":
                return j
            j += 1
        return -1

    def _check_forced_operator(self, start: int, end: int) -> None:
        if start < self._suppress_until:
            return
        operator = _FORCED_KEYWORDS.get(self.text[start:end])
        if operator is None:
            return
        if start > 0 and self.text[start - 1] in _NON_KEYWORD_PREFIXES:
            return
        if end >= self.length or self.text[end] != "!":
            return
        after = self.text[end + 1 : end + 2]
        if after in _OPERATOR_CHARACTERS and not self.text.startswith(("//", "/*"), end + 1):
            return
        line, column = self._position(start)
        self.uses.append(SwiftOperatorUse(operator=operator, line=line, column=column))

    def _skip_block_comment(self, i: int) -> int:
        depth = 0
        while i < self.length:
            if self.text.startswith("/*", i):
                depth += 1
                i += 2
            elif self.text.startswith("*/", i):
                depth -= 1
                i += 2
                if depth == 0:
                    return i
            else:
                i += 1
        return i

    def _skip_hash_literal(self, i: int) -> int:
        """At '#': a raw string (#"), extended regex (#/), or other '#' syntax."""
        hashes = 0
        while i + hashes < self.length and self.text[i + hashes] == "#":
            hashes += 1
        following = self.text[i + hashes : i + hashes + 1]
        if following == '"':
            self._last = '"'
            return self._skip_string(i + hashes, hashes=hashes)
        if following == "/":
            close = "/" + "#" * hashes
            end = self.text.find(close, i + hashes + 1)
            self._last = "a"
            return self.length if end < 0 else end + len(close)
        self._last = "#"
        return i + hashes  # #if, #selector, #available, shebang, ...

    def _skip_string(self, i: int, *, hashes: int) -> int:
        """At the opening quote(s); return the index after the closing delimiter."""
        text = self.text
        multiline = text.startswith('"""', i)
        i += 3 if multiline else 1
        close = ('"""' if multiline else '"') + "#" * hashes
        escape = "\\" + "#" * hashes
        while i < self.length:
            if text.startswith(escape, i):
                i += len(escape)
                if i < self.length and text[i] == "(":
                    i = self.scan_code(i + 1, record=False, until_close_paren=True)
                elif i < self.length and text[i] != "\n":
                    i += 1
            elif text.startswith(close, i):
                return i + len(close)
            elif text[i] == "\n" and not multiline:
                return i  # unterminated single-line string: recover at line end
            else:
                i += 1
        return i
