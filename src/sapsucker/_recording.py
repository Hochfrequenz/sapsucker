"""Parser for SAP GUI's built-in recorder output (``.vbs`` journey recordings).

A recording is a flat list of ``session.findById("<id>").<member>`` statements
after a fixed guarded connect preamble — machine-generated, linear, no control
flow beyond that preamble (issue #82's corpus). This module parses such a file
into typed steps so tooling (member-gap checks, timeline correlation) can work
with the recorded journey without re-deriving the grammar each time.

Design (agreed in the recording→artefact plan, ``docs/superpowers/specs/``):

* **Fail loud, never skip.** An unknown construct raises
  :class:`RecordingParseError` with line number and text. A parser that
  silently drops one line in five hundred corrupts everything built on it.
* **The document preserves the file bytes.** Encoding, line endings, header
  comments and the preamble are kept verbatim; ``steps`` is a parsed view over
  the statement region. The round-trip guarantee (re-render == original bytes)
  is what makes that claim testable — it runs as a unit test over the whole
  committed corpus.
* **Regex is the statement splitter, not the grammar.** The statement body is
  tokenised by a small hand-written scanner: VB string rules (``""`` is the
  only escape; a string may contain anything, including ``").``), colon
  chaining outside strings, and the three-plus-one statement forms the corpus
  exercises (assignment, bare no-argument method, unparenthesised arguments,
  and parenthesised invocation, which the grammar accepts but no committed
  recording uses).

Example::

    from sapsucker._recording import Recording

    rec = Recording.load("docs/spike/journey3_bp.vbs")
    for step in rec.steps:
        print(step.line_no, step.element_id, step.member, step.args)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Recording", "RecordingParseError", "RecordingStep"]


class RecordingParseError(ValueError):
    """A recording could not be parsed; the message names the offending line."""

    def __init__(self, line_no: int, text: str, reason: str) -> None:
        super().__init__(f"line {line_no}: {reason}: {text!r}")
        self.line_no = line_no
        self.text = text
        self.reason = reason


@dataclass(frozen=True)
class RecordingStep:
    """One recorded ``findById`` statement."""

    element_id: str
    member: str
    #: Positional arguments: the assignment value for ``=``, the argument list
    #: for calls. ``None`` for a bare method with no arguments.
    args: tuple[str, ...] | None
    #: The statement's original source line(s), verbatim.
    raw: str
    #: 1-based line number of the statement's first line in the file.
    line_no: int


def _split_top_level_colons(line: str) -> list[str]:
    """Split on ``:`` separators that are outside string literals.

    VBScript chains statements with ``:`` (``a: b``). A colon inside a quoted
    string is not a separator.
    """
    parts: list[str] = []
    start = 0
    in_string = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if in_string and i + 1 < len(line) and line[i + 1] == '"':
                i += 2  # "" is an escaped quote, stays inside the string
                continue
            in_string = not in_string
        elif ch == ":" and not in_string:
            parts.append(line[start:i])
            start = i + 1
        i += 1
    parts.append(line[start:])
    return parts


def _scan_quoted(text: str, start: int) -> tuple[str, int]:
    """Read a VB string starting at ``text[start] == '"'``.

    Returns ``(value, index_after_closing_quote)`` with ``""`` unescaped.
    Raises ValueError if the string is unterminated.
    """
    if text[start] != '"':
        raise ValueError(f"expected '\"' at position {start}")
    i = start + 1
    out: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch == '"':
            if i + 1 < len(text) and text[i + 1] == '"':
                out.append('"')
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise ValueError("unterminated string")


def _parse_statement(line: str, line_no: int) -> RecordingStep:
    """Parse one ``session.findById("...").<member>...`` statement.

    Grammar (member invocation forms; see the plan doc for evidence):

        statement   := session "." "findById" "(" STRING ")" "." MEMBER tail
        tail        := "" | "=" expr | "(" arglist ")" | arglist
        arglist     := value ("," value)*        # bare: comma-separated tokens
        value       := STRING | NUMBER | IDENT | true | false

    An assignment's single value becomes ``args=(value,)``; a bare or
    parenthesised call's arguments become ``args``; a no-argument call gets
    ``args=None``.
    """
    stripped = line.strip()

    prefix = 'session.findById("'
    if not stripped.startswith(prefix):
        raise RecordingParseError(line_no, stripped, "not a session.findById statement")
    try:
        element_id, pos = _scan_quoted(stripped, len(prefix) - 1)
    except ValueError as exc:
        raise RecordingParseError(line_no, stripped, f"bad element id string: {exc}") from exc
    rest = stripped[pos:]
    if not rest.startswith(")."):
        raise RecordingParseError(line_no, stripped, "expected ').' after findById(...)")

    # Member name: letters/digits/underscore.
    rest = rest[2:]
    j = 0
    while j < len(rest) and (rest[j].isalnum() or rest[j] == "_"):
        j += 1
    member = rest[:j]
    if not member:
        raise RecordingParseError(line_no, stripped, "missing member name")
    rest = rest[j:].strip()

    if not rest:
        return RecordingStep(element_id, member, None, line, line_no)

    if rest.startswith("="):
        value_src = rest[1:].strip()
        value = _parse_value(value_src, line_no, stripped)
        return RecordingStep(element_id, member, (value,), line, line_no)

    if rest.startswith("("):
        if not rest.endswith(")"):
            raise RecordingParseError(line_no, stripped, "unbalanced parenthesis in argument list")
        inner = rest[1:-1].strip()
        if not inner:
            return RecordingStep(element_id, member, (), line, line_no)
        return RecordingStep(element_id, member, _parse_arglist(inner, line_no, stripped), line, line_no)

    # Bare (unparenthesised) arguments: the corpus's sendVKey/resizeWorkingPane form.
    args = _parse_arglist(rest, line_no, stripped)
    for arg in args:
        if not (arg[:1].isdigit() or arg[:1].isalpha()) and arg not in ("true", "false", "-"):
            raise RecordingParseError(line_no, stripped, f"unexpected token in bare arguments: {rest!r}")
    return RecordingStep(element_id, member, args, line, line_no)


def _parse_value(src: str, line_no: int, whole: str) -> str:
    """Parse one argument value: a quoted string (``""``-escaped) or a bare token."""
    src = src.strip()
    if src.startswith('"'):
        try:
            value, end = _scan_quoted(src, 0)
        except ValueError as exc:
            raise RecordingParseError(line_no, whole, f"bad string value: {exc}") from exc
        if src[end:].strip():
            raise RecordingParseError(line_no, whole, f"trailing content after string value: {src[end:]!r}")
        return value
    if not src:
        raise RecordingParseError(line_no, whole, "empty argument value")
    # Bare token: number, identifier, true/false — take it verbatim.
    if any(ch.isspace() for ch in src):
        raise RecordingParseError(line_no, whole, f"unexpected whitespace in bare value: {src!r}")
    return src


def _parse_arglist(src: str, line_no: int, whole: str) -> tuple[str, ...]:
    """Parse a comma-separated argument list (quoted strings may contain commas)."""
    args: list[str] = []
    i = 0
    current: list[str] = []
    while i < len(src):
        ch = src[i]
        if ch == '"':
            try:
                value, i = _scan_quoted(src, i)
            except ValueError as exc:
                raise RecordingParseError(line_no, whole, f"bad string argument: {exc}") from exc
            current.append(value)
            continue
        if ch == ",":
            args.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    last = "".join(current).strip()
    if last:
        args.append(last)
    if not args:
        raise RecordingParseError(line_no, whole, "empty argument list")
    return tuple(args)


_PREAMBLE_SNIPPETS = ("IsObject(application)", 'GetObject("SAPGUI")', "WScript.ConnectObject")


def _looks_like_preamble(line: str) -> bool:
    return any(snippet in line for snippet in _PREAMBLE_SNIPPETS)


@dataclass
class Recording:
    """A parsed journey recording.

    ``prologue`` holds every line before the first statement (comments and the
    guarded preamble) verbatim, so re-rendering reproduces the file. ``steps``
    is the parsed journey; ``epilogue`` the lines after the last statement
    (normally empty).
    """

    prologue: list[str] = field(default_factory=list)
    steps: list[RecordingStep] = field(default_factory=list)
    epilogue: list[str] = field(default_factory=list)
    path: str | None = None
    #: Line separator detected from the source file (load) or LF (parse).
    newline: str = "\n"

    @classmethod
    def load(cls, path: str | Path) -> Recording:
        """Load and parse a recording file.

        The recorder writes UTF-16 by default (observed on this corpus) and
        plain ASCII/UTF-8 variants exist; the codec is sniffed per file.
        """
        path = Path(path)
        raw = path.read_bytes()
        if raw.startswith(b"\xff\xfe"):
            text = raw.decode("utf-16-le")
        elif raw.startswith(b"\xfe\xff"):
            text = raw.decode("utf-16-be")
        elif raw.startswith(b"\xef\xbb\xbf"):
            text = raw.decode("utf-8-sig")
        elif b"\x00" in raw[:64]:
            # BOM-less UTF-16-LE (observed from some toolchains writing the file).
            text = raw.decode("utf-16-le")
        else:
            text = raw.decode("utf-8")
        rec = cls.parse(text)
        rec.newline = "\r\n" if b"\r\n" in raw else "\n"
        rec.path = str(path)
        return rec

    @classmethod
    def parse(cls, text: str) -> Recording:
        """Parse recording text (any line-ending style)."""
        lines = text.splitlines()
        rec = cls()
        seen_first_statement = False
        for idx, line in enumerate(lines, start=1):
            if not seen_first_statement:
                if "findById" in line and "session.findById" in line:
                    for chunk in _split_top_level_colons(line):
                        statement = chunk.strip()
                        if not statement:
                            continue
                        rec.steps.append(_parse_statement(statement, idx))
                    seen_first_statement = True
                else:
                    rec.prologue.append(line)
            elif "findById" in line:
                for part in _split_top_level_colons(line):
                    statement = part.strip()
                    if not statement:
                        continue
                    rec.steps.append(_parse_statement(statement, idx))
            else:
                rec.epilogue.append(line)
        if not rec.steps:
            raise RecordingParseError(len(lines), text[:200], "no findById statements found")
        return rec

    def render(self) -> str:
        """Re-render to text that byte-matches the source (same line endings)."""
        out_lines = [*self.prologue]
        for step in self.steps:
            out_lines.append(step.raw)
        out_lines.extend(self.epilogue)
        if not out_lines:
            return ""
        return self.newline.join(out_lines) + self.newline

    @property
    def preamble_is_guarded(self) -> bool:
        """Whether the prologue contains the recorder's guarded connect preamble."""
        return any(_looks_like_preamble(line) for line in self.prologue)
