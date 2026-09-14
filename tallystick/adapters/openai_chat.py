"""Read an OpenAI Chat Completions message list as a raw tallystick trace.

    tallystick check-trace --from openai my_log.json
    tallystick convert     --from openai my_log.json -o trace.json

This is the shape most people already have: the `messages` array you sent to
the API plus everything that came back, with `role` in `system`, `developer`,
`user`, `assistant`, `tool`. LiteLLM, vLLM and the OpenAI-compatible gateways
use the same keys, so their logs load unchanged.

The Anthropic Messages API does not use the same keys, and the difference is
dangerous rather than cosmetic: it returns a tool's output as a `tool_result`
block inside a **`user`** message. Read naively, that is a `document` - the
strongest kind of root - and the audit would take a tool's digest for external
evidence. So the `tool_result` and `tool_use` blocks are recognised and split
back out into `tool` messages and tool calls before anything is recorded. That
path is covered by tests built from the documented block shapes; it has not
been run against a corpus of real logs from those gateways.

How it becomes a trace
----------------------
  system / developer / user -> root artifact  (document)        `m{k}`
  assistant.content         -> derived artifact (intermediate)  `a{k}`
  the last model text       -> derived artifact (final_answer)  (renamed in place)
  tool                      -> root artifact  (tool_result)     `t{k}`

A step is one assistant turn: `a{k}` consumes every artifact recorded before
it, because that is what the API was sent. Its tool calls are a separate step
`a{k}.tools` with the same inputs plus the turn's own text, producing the tool
results that follow. That is the honest record of a chat loop: each call
carries the whole conversation so far, and a claim written in one turn may be
credited against anything earlier in it.

Where it can be wrong, and what to do about it
----------------------------------------------
**A tool that hands the model's own words back is not a root.** A `final_answer`
tool, a notes store, a scratchpad, a code interpreter echoing a string literal
from the model's own code - none of those brought anything in from outside, and
recording them as roots launders the model's text into evidence. The message
list does not say which tools do this, so pass their names:

    to_trace(messages, model_text_tools={"final_answer", "save_note"})

**A tool that returns a page as fetched is a `document`, not a `tool_result`.**
A retriever handing back the passage, a file reader handing back the file: that
is external text stored verbatim, which is what a root should be. A web search
API answering with its own summary is not. The adapter cannot tell them apart
either, so pass the ones that return text as fetched:

    to_trace(messages, verbatim_tools={"read_file", "fetch_url"})

Both default to empty, which is the conservative reading: every tool result is
a `tool_result`, the audit stops there, and `check-trace` counts it against the
recording rather than silently in its favour.

**One reading overrides `verbatim_tools`, and it has to be said here.** A
result that hands back text from the arguments of the call it answered - as its
last line, or as at least half of it anywhere inside it - is read as the
model's own text even when the tool was declared verbatim - the
model wrote that line, whatever the tool usually does. That is right for
`echo '<the model's own paragraph>'` and wrong for `touch f && ls` returning
the filename it was given. Every override is named in
`_meta.echoed_back_tool_results` with the line it fired on, so the decision can
be checked rather than trusted.

**A tool that hands back text from ANOTHER call is reported, not demoted.**
An interpreter that kept a variable, a notes store, a file written in one turn
and read in the next - or saved and read back inside one turn: the text is the
model's, but the same match also happens when a tool confirms what the model
guessed, and the log cannot tell the two apart. So is a result the reading could
not match to any call (a gateway renamed the tool, or rewrote the id): nothing
says which arguments to weigh it against. Such results stay evidence and are
recorded in `_meta.echo_warning_details`, and as text in
`_meta.echoes_from_earlier_turns` (a name kept for the files already written),
each with the message's position in the FILE, counted from 0, and the call id
where the log has one. If the tool is one that hands text back, say so with
`model_text_tools`. `check-trace` and `audit` exit 1 on any such report, with
the reason `unreviewed_echo_warnings`, and `tallystick.audit()` raises, until
each tool is confirmed by name with `--accept-echo-warning NAME`: a warning
nobody read must not pass a run.

**A tool result longer than `max_tool_chars` is cut**, and the cut is recorded
in `_meta.truncated` so the audit can say "not recorded" rather than "not
supported". Nothing else is altered.

Everything this reader leaves out of the trace it writes down: `_meta` carries
`skipped_empty` (messages with no text), `dropped_messages` (roles it does not
know) and `truncated`. A reader that drops silently is worse than one that
cannot read the file at all, because the audit then reports on a run that is
not the one that happened.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

DEFAULT_MAX_TOOL_CHARS = 20_000

#: Roles whose content entered the run from outside the assistant.
_ROOT_ROLES = frozenset({"system", "developer", "user"})

#: Roles this reader understands. Anything else is recorded as dropped rather
#: than guessed at: a role we cannot place is a role we cannot say is a root.
_KNOWN_ROLES = _ROOT_ROLES | {"assistant", "tool", "function"}



def _args_text(raw) -> str:
    """The arguments a tool call carried, as text. OpenAI sends them as a JSON
    string, the Anthropic shape as an object; both are compared as text."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(raw)




#: Below this, a line only counts when it stands in the model's text as a whole
#: token - see `_hands_back_what_it_was_given`. Above it, appearing at all is
#: enough: a sentence of this length does not turn up inside someone else's
#: writing by accident.
ECHO_MIN_CHARS = 8

_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f',
            '"': '"', "'": "'", '/': '/', '\\': '\\'}


def _hex(digits: str) -> int:
    """`digits` read as hexadecimal, or -1 unless every character is a hex
    digit. `int(s, 16)` alone would also accept a sign, spaces and underscores,
    and decode text that was never an escape."""
    if not digits or any(c not in "0123456789abcdefABCDEF" for c in digits):
        return -1
    return int(digits, 16)


def _unescape(text: str) -> str:
    """A best-effort reading of the backslash escapes any JSON writer may have
    used, for COMPARISON ONLY - the recorded artifact is never touched by this.

    Different encoders spell the same string differently: Python's default
    writes non-ASCII as `\\uXXXX`, Go escapes `&`, `<` and `>` the same way,
    PHP and several Java libraries escape `/`, and a model's own code escapes
    its quotes before the JSON layer escapes them again. Enumerating the
    spellings meant chasing each writer in turn and missing the next one;
    reading the escapes instead handles them together, and anything it fails to
    decode is left exactly as it stands.

    Decoding a string can change it, which is why it is confined to this
    comparison: the decision it feeds can only move an artifact OUT of the root
    set, so a decoding mistake makes the audit stricter, never laxer.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch != '\\' or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt == 'u' and i + 5 < n:
            code = _hex(text[i + 2:i + 6])
            if code >= 0:
                # An emoji or a mathematical letter is written as a PAIR of
                # codes - a surrogate pair. Reading them apart leaves two
                # fragments that match nothing, which is how emoji echoes got
                # back through after this decoding replaced the old one.
                if 0xD800 <= code <= 0xDBFF and text[i + 6:i + 8] == '\\u':
                    low = _hex(text[i + 8:i + 12])
                    if 0xDC00 <= low <= 0xDFFF:
                        out.append(chr(0x10000 + ((code - 0xD800) << 10)
                                       + (low - 0xDC00)))
                        i += 12
                        continue
                out.append(chr(code))
                i += 6
                continue
        # Two spellings JSON never writes but a model's own Python code does:
        # `\U0001F1E6` for a character outside the Basic Multilingual Plane and
        # `\xfc` for ü. Inside a JSON string the backslash arrives doubled, so
        # these decode on the second pass, after the first has undone the JSON.
        if nxt == 'U' and i + 9 < n:
            code = _hex(text[i + 2:i + 10])
            if 0 <= code <= 0x10FFFF:
                out.append(chr(code))
                i += 10
                continue
        if nxt == 'x' and i + 3 < n:
            code = _hex(text[i + 2:i + 4])
            if code >= 0:
                out.append(chr(code))
                i += 4
                continue
        if nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _is_whole_token(haystack: str, needle: str) -> bool:
    """Does `needle` stand in `haystack` on its own, rather than inside a longer
    word or number? Checked by what sits either side: a letter or digit there
    means the match is part of something else. A quoted string followed by a
    colon is a JSON key - the caller's vocabulary, not a value it stated."""
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before = haystack[start - 1] if start else ""
        after = haystack[end] if end < len(haystack) else ""
        key = before == '"' and haystack[end:end + 2] == '":'
        if not before.isalnum() and not after.isalnum() and not key:
            return True
        start = haystack.find(needle, start + 1)
    return False


def _matched_line(result: str) -> str:
    """The line the echo test looked at - the last non-empty one."""
    lines = [ln.strip() for ln in result.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _short(text: str, limit: int = 80) -> str:
    """A quotation short enough for a terminal, long enough to check."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"

def _echo_line(result: str) -> str:
    """The line an echo test weighs - the last non-empty one - or "" when the
    result has no line that could be an echo at all.

    Why the LAST line and not the whole result: an interpreter prints its work
    before its answer, and a search tool often echoes the query in a header. A
    header match would demote real evidence; a final-line match is the shape of
    a value handed back."""
    lines = [ln.strip() for ln in result.splitlines() if ln.strip()]
    if not lines:
        return ""
    last = lines[-1]
    # A line with no letter or digit is punctuation, not an answer.
    if not any(ch.isalnum() for ch in last):
        return ""
    # A single character is a fragment unless it is the whole reply. A stray
    # trailing "e" at the end of a Wikipedia dump matched the letter "e" in the
    # model's own writing and demoted the only real evidence in that run; a
    # tool whose entire reply is "0" is a different thing, and stays caught.
    if len(last) < 2 and result.strip() != last:
        return ""
    return last


def _spellings(text: str) -> Tuple[str, str, str]:
    """`text` as written, unescaped once, and unescaped twice. Twice, because
    two layers happen: the model's own code escapes its quotes and the JSON
    layer escapes them again. A third layer has no example behind it."""
    once = _unescape(text)
    return text, once, _unescape(once)


def _stands_in(line: str, spellings: Iterable[str]) -> bool:
    """Does `line` stand in any of these spellings of the model's text? Below
    ECHO_MIN_CHARS it must stand as a whole token - `"B"` in `answer = "B"`
    does, the comma in `{"city":"Paris","units":"m"}` does not, and neither does
    a JSON key. Above it, appearing at all is enough: a sentence that long does
    not turn up inside someone else's writing by accident."""
    for haystack in spellings:
        if line not in haystack:
            continue
        if len(line) >= ECHO_MIN_CHARS or _is_whole_token(haystack, line):
            return True
    return False


_LITERAL_DQ = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_LITERAL_SQ = re.compile(r"'((?:[^'\\\n]|\\.)*)'")
_WORD = re.compile(r"[^\W_]+")


def _values(obj: Any) -> Iterator[str]:
    """Every string and number inside parsed call arguments, as text."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield str(obj)
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _values(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _values(value)


def _norm(text: str) -> str:
    """`text` with every run of whitespace made one space: a paragraph reflowed
    onto one line is still the same paragraph."""
    return " ".join(text.split())


_NOT_ALNUM = re.compile(r"[\W_]+")


def _alnum(text: str) -> int:
    """How many letters and digits `text` holds - the measure of how much of a
    result a piece of it is, which punctuation and JSON braces should not move."""
    return len(_NOT_ALNUM.sub("", text))


@functools.lru_cache(maxsize=512)
def _pieces(args: str) -> frozenset:
    """The whole values, lines, string literals and words of one call's
    arguments, each also with its whitespace collapsed - what a result is looked
    up in: for an echo from another call of the turn or of an earlier turn, and,
    among calls the log cannot tell apart, for the one it answered.

    A lookup, not a scan, because these checks weigh many calls. Scanning
    everything written before for every result is quadratic in the length of
    the run - a 400-turn CodeAct log took four seconds that way. Each call is
    read once (hence the cache), and each result costs a handful of set lookups.
    The cost is reach: a line the model wrote only inside a longer string is not
    found here.

    Arguments are parsed as JSON where they are JSON, so a literal inside the
    model's code is read with its quotes where the model put them, and each
    string is also read with its own escapes undone."""
    try:
        texts = list(_values(json.loads(args)))
    except (TypeError, ValueError, RecursionError):
        texts = []
    if not texts:
        texts = list(_spellings(args))
    texts += [_unescape(t) for t in texts if "\\" in t]
    found: Set[str] = set()
    for text in texts:
        whole = _norm(text)
        if whole:
            found.add(whole)
        for ln in text.splitlines():
            ln = ln.strip()
            if ln:
                found.add(ln)
                found.add(_norm(ln))
        for pattern in (_LITERAL_DQ, _LITERAL_SQ):
            for match in pattern.finditer(text):
                literal = match.group(1).strip()
                if literal:
                    found.add(literal)
                    found.add(_norm(literal))
                    if "\\" in literal:
                        found.add(_norm(_unescape(literal)))
        found.update(_WORD.findall(text))
    return frozenset(found)


def _contains_own_text(result: str, sent: str) -> bool:
    """Does a phrase from the call's own arguments stand inside its result as a
    whole unit, and make up most of it?

    The answering call's text is looked for INSIDE the result, not only as its
    last line: as a JSON value (`{"echo": "<text>"}`, indented JSON ending in
    `}`), as a line followed by `[Execution time: 0.01s]`, after a label
    (`Saved note: <text>`), as a quoted literal (`'<text>'` from a REPL), or as
    the whole result reflowed onto one line. See `_echo_candidates`.

    Not as an arbitrary substring. Measured on AgentHallu, a bare "a piece of the
    arguments makes up half of the result" demoted 101 more results than the
    last-line rule, and among them real facts the tool reported about the
    argument: `rm: cannot remove 'X': No such file or directory`,
    `{"matches": ["./project/test_results.json"]}`, `'findings_report' removed`.
    So the unit must be a phrase - it holds a space - because identifiers, paths
    and URLs are what a tool legitimately repeats in a status or an error, and a
    sentence the model wrote is what a tool hands back. And it must be more than
    half of the result's letters and digits, which keeps a search result that
    quotes its query in a header as evidence."""
    pieces = _pieces(sent)
    return any(len(c) >= ECHO_MIN_CHARS and " " in c and c in pieces
               for c in _echo_candidates(result))


_TRAILING = ".,;:!?"


#: Results longer than this are not unescaped for the candidate search: decoding
#: is a character-by-character pass, and a long page that is ALSO escaped JSON
#: is not the shape of a value handed back.
UNESCAPE_RESULT_CHARS = 2000


@functools.lru_cache(maxsize=256)
def _echo_candidates(result: str) -> Tuple[str, ...]:
    """The strings in a result that could be text the model handed in, with
    whitespace collapsed, each holding MORE than half of the result's letters
    and digits: the whole result, each line, a line without its trailing
    punctuation or without a `label: ` in front, each quoted literal, and each
    JSON value.

    That is how a note read back as `{"text": "..."}`, as indented JSON, as
    `capital: ...`, with a period added or a status line after it, or as `'...'`
    is found by lookup, in time linear in the result. The half rule keeps a page
    that merely contains a line the model once wrote from being called its echo;
    it also lets a line of a long page be skipped by its length alone, before
    anything is counted. Cached: the rule and the warnings ask about the same
    result."""
    found: List[str] = []
    seen: Set[str] = set()
    texts = [result]
    if "\\" in result and len(result) <= UNESCAPE_RESULT_CHARS:
        texts.append(_unescape(result))
    for text in texts:
        total = _alnum(text)
        if not total:
            continue

        def add(piece: str, total: int = total) -> None:
            if 2 * len(piece) <= total:
                return              # too short to be most of the result
            piece = _norm(piece)
            if piece and piece not in seen and 2 * _alnum(piece) > total:
                seen.add(piece)
                found.append(piece)

        add(text)
        floor = total // 2          # no string this long or shorter is most of it
        for ln in text.splitlines():
            if len(ln) <= floor:
                continue            # skipped by length, before anything is counted
            ln = ln.strip()
            add(ln)
            add(ln.rstrip(_TRAILING))
            _label, sep, rest = ln.partition(": ")
            if sep:
                add(rest)
        for pattern in (_LITERAL_DQ, _LITERAL_SQ):
            for match in pattern.finditer(text):
                if len(match.group(1)) > floor:
                    add(match.group(1))
        stripped = text.strip()
        if stripped[:1] in ("{", "[", '"'):
            try:
                values = list(_values(json.loads(stripped)))
            except (TypeError, ValueError, RecursionError):
                values = []
            for value in values:
                add(value)
    return tuple(found)


def _stands_among(result: str, piece_sets: Iterable[frozenset]) -> bool:
    """Is some candidate of `result` a piece of one of these calls?"""
    sets = [s for s in piece_sets if s]
    return bool(sets) and any(c in s for c in _echo_candidates(result) for s in sets)


class _Call:
    """One declared tool call, its arguments read into pieces once, when it is
    declared. `named` is False where the log gave no name and the reader's
    placeholder `tool` stands in for one."""

    __slots__ = ("cid", "name", "named", "args", "pieces", "done")

    def __init__(self, cid: str, name: str, named: bool, args: str):
        self.cid, self.name, self.named, self.args = cid, name, named, args
        self.pieces = _pieces(args)
        self.done = False


#: How each kind of warning is worded in the text a person reads.
_WARNING_KINDS = {
    "earlier_turn": "a line the model wrote in an earlier call",
    "same_turn": "a line the model wrote in another call of this turn",
    "unmatched": "a result the reading matched to no call",
}


def _hands_back_what_it_was_given(result: str, sent: str) -> bool:
    """Did this tool return text the model wrote into the call it answered?

    `sent` is the arguments of that call, and empty where the log does not say
    which of several calls a result answers - no id, and more than one open call
    it could be. `to_trace` then looks the result up among the pieces of every
    call it could be (`_stands_among`) and marks such a demotion uncertain: it is
    never elected as the answer. This is asked once per tool result either way,
    which tests/test_openai_roundtrip_numbers.py counts on.

    Two tests, and either one demotes: the result's last line stands in the
    arguments (`_stands_in`), or a phrase from the arguments stands inside the
    result as a whole unit and makes up most of it (`_contains_own_text`).

    Why only the answering call. Three widths were tried on the same inputs
    (tests/test_openai_chat_wide_context.py, tests/test_openai_chat_turn_width.py).
    The whole run destroyed real evidence: a search confirming a candidate the
    model had named, a computed `True` where earlier code said `return True`.
    The turn did the same between unrelated calls declared together - two
    translations demoting each other - and still missed an interpreter that
    kept a variable from an earlier turn. The answering call is the one place
    where a match means the model gave this tool the text it got back. The six
    literal shapes that once slipped past this width (a literal held in a
    variable, an unquoted number, `"x".strip()`, `r"..."`, triple quotes, a call
    split across lines) were defeated by a quoting check, not by the width, and
    all six are caught here.

    An echo from another call - a stateful interpreter, a notes store, a file
    written then read back, in an earlier turn or in the same one - is not
    demoted, and neither is a result matched to no call. `to_trace` looks the
    result's candidates (`_echo_candidates`) up among the pieces of the other
    calls (`_pieces`) and REPORTS a match, or the missing match, in
    `_meta.echo_warning_details`: the log cannot tell a value handed back from a
    value confirmed, and the operator can, with --tool-returns-model-text.

    Why this rule is allowed to be wrong. It can only move an artifact OUT of
    the root set - from evidence to model text. A mistake makes the audit
    stricter (a claim loses a source it might have had), never laxer (model text
    recorded as a source, which is the failure this library exists to catch).
    That asymmetry is the whole licence for running it without the operator
    declaring anything, so any change that gives it a second effect voids the
    licence until the argument is made again. Electing an uncertain demotion as
    the answer would be such an effect, which is why it is barred.

    What it costs, counted afresh on AgentHallu for proverka6 (the corpus as
    bench/openai_roundtrip.py renders it). It reads 216 of 3535 tool results
    (6.1%) as the model's own words, the same with and without flags, and by
    itself catches 136 of the 460 results of the corpus's four echo tools. It
    reports 26 results as possible echoes kept as evidence with no flags (24 from
    an earlier turn, 2 from the same turn), and 6 with the four echo tools
    declared (4 and 2). Against the reader written for that corpus by hand, with
    those four declared, it is stricter on 60 artifacts and laxer on none. Every
    decision is named in `_meta` together with the line it fired on, so it can
    be checked rather than trusted.
    """
    if not sent:
        return False
    last = _echo_line(result)
    if last and _stands_in(last, _spellings(sent)):
        return True
    return _contains_own_text(result, sent)


def _text(value: Any, _depth: int = 0) -> str:
    """Content may be a string, or the content-parts list the vision and tool
    APIs use, or - inside an Anthropic `tool_result` block - a nested list of
    those. Parts that are not text (an image, a file id) have no text to audit,
    so they are dropped, and the caller records what kind was dropped."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                # OpenAI spells it `text`; the OTel GenAI parts spell the same
                # thing `content`. Both are read so a part list from either
                # side of the wire loads without a second reader.
                for key in ("text", "content"):
                    inner = part.get(key)
                    if isinstance(inner, str):
                        parts.append(inner)
                        break
                    if isinstance(inner, list) and _depth < 4:
                        nested = _text(inner, _depth + 1)
                        if nested:
                            parts.append(nested)
                        break
        return "\n".join(p for p in parts if p)
    return json.dumps(value, ensure_ascii=False)


def _nontext_parts(value: Any) -> List[str]:
    """The kinds of part that carried no text, so the caller can say what it
    left out instead of claiming the message held nothing."""
    kinds: List[str] = []
    if isinstance(value, list):
        for part in value:
            if isinstance(part, dict) and not any(
                    isinstance(part.get(k), (str, list)) for k in ("text", "content")):
                kinds.append(str(part.get("type") or "part"))
    return kinds


def _expand(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Normalise the dialects into one shape before anything is recorded.

    The whole point is the Anthropic case: a tool's output arrives as a
    `tool_result` block on a **user** message, and a user message is a root. If
    that is not split back out here, the audit accepts a tool's digest as
    external evidence - the laundering this project exists to catch, committed
    by its own reader. Also handles the pre-2024 `function_call` field, whose
    assistant turn otherwise carries no name for the result that follows.

    Returns the normalised messages and a note for each rewriting done, so the
    reading is visible rather than magic."""
    out: List[Dict[str, Any]] = []
    notes: List[str] = []
    # Every message this returns carries `_file_index`, its position in the
    # list the person has on disk, so a warning can point at THEIR file: once a
    # message with two tool_result blocks is split, positions after it move.
    for file_index, message in enumerate(messages):
        role = str(message.get("role", ""))
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        types = {str(b.get("type", "")) for b in blocks if isinstance(b, dict)}

        if role in _ROOT_ROLES and "tool_result" in types:
            # The results come first: they answer the calls of the turn before,
            # and any text in the same message was written after them.
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out.append({"_file_index": file_index, "role": "tool",
                                "tool_call_id": str(b.get("tool_use_id")
                                                    or b.get("id") or ""),
                                "name": str(b.get("name") or ""),
                                "content": b.get("content")})
            keep = [b for b in blocks
                    if not (isinstance(b, dict) and b.get("type") == "tool_result")]
            # Kept even when it holds no text, so the main loop can record what
            # kind of part it was: a remainder dropped here would vanish from
            # `skipped_empty` and `dropped_messages` both.
            if keep:
                out.append({**message, "content": keep, "_file_index": file_index})
            notes.append(f"a tool_result block on a {role} message was read as a "
                         f"tool result, not as a root")
            continue

        if role == "assistant" and ("tool_use" in types
                                    or isinstance(message.get("function_call"), dict)):
            calls = list(message.get("tool_calls") or [])
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    calls.append({"id": str(b.get("id") or ""), "type": "function",
                                  "function": {"name": str(b.get("name") or "tool"),
                                               "arguments": b.get("input")}})
            # Both dialects in one message is odd but happens through gateways,
            # and dropping either would leave a result with no name - which is
            # exactly how --tool-returns-model-text goes silently inert.
            fn = message.get("function_call")
            if isinstance(fn, dict):
                calls.append({"id": str(fn.get("id") or ""), "type": "function",
                              "function": {"name": str(fn.get("name") or "tool"),
                                           "arguments": fn.get("arguments")}})
            keep = [b for b in blocks
                    if not (isinstance(b, dict) and b.get("type") == "tool_use")]
            content = keep if blocks else message.get("content")
            out.append({**message, "content": content, "tool_calls": calls,
                        "_file_index": file_index})
            continue

        out.append({**message, "_file_index": file_index})
    return out, sorted(set(notes))


def _messages_of(data: Any) -> List[Dict[str, Any]]:
    """Accept a bare list, `{"messages": [...]}`, or a response object with
    `choices`, so that the thing a person actually has on disk loads."""
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get("messages")
        if raw is None and isinstance(data.get("choices"), list):
            raw = [c.get("message") for c in data["choices"] if isinstance(c, dict)]
        if raw is None:
            raise ValueError(
                "no 'messages' array: an OpenAI chat log is a list of messages, or "
                "an object with a 'messages' key")
    else:
        raise ValueError(f"expected a list or an object, got {type(data).__name__}")
    if not isinstance(raw, list):
        raise ValueError(f"'messages' must be a list, got {type(raw).__name__}")
    out = [m for m in raw if isinstance(m, dict)]
    if not out:
        raise ValueError("no messages in this log")
    if not any("role" in m for m in out):
        raise ValueError(
            "no message has a 'role': this does not look like a chat message list")
    return out


def looks_like_openai(data: Any) -> bool:
    """Cheap shape test, used to suggest `--from openai` when a file will not
    load as a trace. Never used to convert silently: a wrong guess about what a
    file is would be a worse failure than asking."""
    try:
        messages = _messages_of(data)
    except ValueError:
        return False
    return any(m.get("role") in _KNOWN_ROLES for m in messages)


def to_trace(data: Any, *, name: str = "",
             max_tool_chars: int = DEFAULT_MAX_TOOL_CHARS,
             model_text_tools: Optional[Iterable[str]] = None,
             verbatim_tools: Optional[Iterable[str]] = None,
             external_tools: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Return a raw trace dict (`artifacts`, `steps`, `_meta`) for a chat log.

    `external_tools` are tools the operator declares as returning external
    evidence in their own words. The declaration clears the WARNINGS about such
    a tool - an echo from another call, a result matched to no call - because
    those are guesses, and the operator is taking on what this reader cannot
    know. It never clears a DEMOTION: a result found in the arguments of the
    very call it answers is a finding, not a guess, and one flag must not switch
    off the only part of the protection that works for certain."""
    if max_tool_chars < 1:
        raise ValueError("max_tool_chars must be at least 1")
    messages, notes = _expand(_messages_of(data))
    echo: Set[str] = set(model_text_tools or ())
    verbatim: Set[str] = set(verbatim_tools or ())
    external: Set[str] = set(external_tools or ())
    overlap = sorted(echo & verbatim)
    if overlap:
        raise ValueError(
            f"{overlap} is named as both a tool that returns the model's own text "
            f"and a tool that returns external text verbatim; it cannot be both")
    overlap = sorted(external & (echo | verbatim))
    if overlap:
        raise ValueError(
            f"{overlap} is declared as returning external evidence and also as "
            f"returning the model's own text or text verbatim; it cannot be both")

    artifacts: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    truncated: List[str] = []
    dropped: List[str] = []
    skipped_empty: List[str] = []
    seen: List[str] = []
    # Tool results are matched to the call that produced them by id where the
    # log has one, so the name is known even when the tool message omits it.
    tool_names: Dict[str, str] = {}
    # Indices into `artifacts` of every artifact holding the model's own words,
    # in order. The last of them is what the user saw - and it may come from a
    # `final_answer` tool, not from an assistant message, which is why this is
    # tracked over artifacts rather than over assistant turns.
    model_text: List[int] = []
    unsure_ids: Set[str] = set()

    pending_tools: List[str] = []
    last_turn: Optional[str] = None
    # Names declared by the calls of the turn currently in flight, in order, so
    # a tool message that carries no id and no name of its own can still be
    # matched - otherwise --tool-returns-model-text is silently inert on the
    # very logs that need it most.
    # Not cleared by flush_tools: a root message between two tool results
    # (a `user` "hurry up" arriving mid-batch) must not realign the queue.
    # The calls of the turn in flight, in declaration order. A call is marked
    # answered, not removed, and indexed by id and by name, so placing a result
    # costs the same in a turn of 800 calls as in a turn of 2.
    open_calls: List[_Call] = []
    head = 0                                  # the first call not yet answered
    by_id: Dict[str, _Call] = {}
    by_name: Dict[str, List[_Call]] = {}
    name_cursor: Dict[str, int] = {}
    open_count: Dict[str, int] = {}           # unanswered calls, per tool name
    open_with_id = 0                          # unanswered calls that carry an id
    calls_by_id: Dict[str, _Call] = {}        # every call seen, by id, any turn
    doubt: Set[str] = set()
    reused_ids: Set[str] = set()
    batch_ids: Set[str] = set()
    guessed: List[str] = []
    unmatched: List[str] = []
    unresolved: List[str] = []
    echoed_back: List[str] = []   # results that quoted their own call back
    # Results whose last line the model wrote into a call of an EARLIER turn:
    # kept as evidence and reported - see `_hands_back_what_it_was_given`.
    earlier_echoes: List[str] = []
    # The same warnings as data. `check-trace` confirms them by tool name, and a
    # name parsed back out of the text above could be forged by a tool's name.
    earlier_echo_details: List[Dict[str, str]] = []
    # Every call of the turn now open, answered or not: how many per name, and
    # the pieces of their arguments, all together and per name.
    batch_count: Dict[str, int] = {}
    batch_pieces: Set[str] = set()
    batch_pieces_by_name: Dict[str, Set[str]] = {}
    # The pieces of every call from turns already closed. Each call is read
    # once, when it is declared, and each result is a set lookup - so reading a
    # long run stays linear, not quadratic.
    earlier_pieces: Set[str] = set()
    # Artifacts demoted against more than one call the log could not tell apart.
    # They are the model's text, but not known to be the one the user saw.
    unconfident_ids: Set[str] = set()
    step_ids: Set[str] = set()

    def step_id(base: str) -> str:
        """Two tool batches under one turn must not share an id: the report
        names steps, and a name that points at two things names neither."""
        sid, n = base, 1
        while sid in step_ids:
            n += 1
            sid = f"{base}_{n}"
        step_ids.add(sid)
        return sid

    def flush_tools() -> None:
        nonlocal pending_tools
        if pending_tools:
            steps.append({"step_id": step_id(f"{last_turn or 'start'}.tools"),
                          "kind": "tool",
                          "inputs": list(seen), "outputs": list(pending_tools)})
            seen.extend(pending_tools)
            pending_tools = []

    for k, message in enumerate(messages):
        role = str(message.get("role", ""))
        content = _text(message.get("content"))

        dropped_kinds = _nontext_parts(message.get("content"))
        if dropped_kinds:
            dropped.append(f"{role or 'unknown'}[{k}] parts without text: "
                           + ", ".join(sorted(set(dropped_kinds))))

        if role in _ROOT_ROLES:
            flush_tools()
            if not content.strip():
                skipped_empty.append(f"{role}[{k}]")
                continue
            aid = f"m{k}"
            artifacts.append({"artifact_id": aid, "kind": "document",
                              "title": role, "content": content})
            seen.append(aid)
            continue

        if role == "assistant":
            flush_tools()
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                calls = []
            if content.strip():
                aid = f"a{k}"
                artifacts.append({"artifact_id": aid, "kind": "intermediate",
                                  "title": "assistant", "content": content})
                steps.append({"step_id": aid, "kind": "generate",
                              "inputs": list(seen), "outputs": [aid]})
                seen.append(aid)
                model_text.append(len(artifacts) - 1)
                last_turn = aid
            else:
                if message.get("content") is not None:
                    skipped_empty.append(f"assistant[{k}]")
                if calls:
                    last_turn = f"a{k}"
            if calls:
                # Doubt belongs to the queue, and ends only where the queue is
                # replaced. Clearing it anywhere else - on a root message, or
                # on an assistant turn that declared nothing - leaves the queue
                # aligned and the uncertainty gone, so the next guess looks
                # certain and an echo goes back to being a root. That was two
                # separate regressions in two rounds.
                open_calls = []
                head = 0
                by_id, by_name, name_cursor = {}, {}, {}
                open_count, batch_count = {}, {}
                open_with_id = 0
                doubt = set()
                batch_ids = set()
                # The turn before this one is closed: its arguments become what
                # a later result is checked against for an echo from earlier.
                earlier_pieces |= batch_pieces
                batch_pieces = set()
                batch_pieces_by_name = {}
            for call in calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                cid = str(call.get("id") or call.get("tool_call_id") or "")
                given = str(fn.get("name") or call.get("name") or "")
                named = given or "tool"
                args_sent = _args_text(fn.get("arguments")
                                      if "arguments" in fn else call.get("arguments"))
                entry = _Call(cid, named, bool(given), args_sent)
                open_calls.append(entry)
                by_name.setdefault(named, []).append(entry)
                open_count[named] = open_count.get(named, 0) + 1
                batch_count[named] = batch_count.get(named, 0) + 1
                batch_pieces |= entry.pieces
                batch_pieces_by_name.setdefault(named, set()).update(entry.pieces)
                if cid:
                    open_with_id += 1
                    by_id.setdefault(cid, entry)
                    calls_by_id[cid] = entry
                    if tool_names.get(cid, named) != named or cid in batch_ids:
                        reused_ids.add(cid)
                    batch_ids.add(cid)
                    tool_names[cid] = named
            continue

        if role in ("tool", "function"):
            cid = str(message.get("tool_call_id") or message.get("id") or "")
            named_itself = str(message.get("name") or "")

            # Which call this result answers, in order of how much the log
            # actually says: its own id, then its own name, then its position.
            #
            # The position tier is a guess, and five rounds of review found
            # five different ways for a guess here to record the model's own
            # words as evidence - each one opened by the repair of the last.
            # So the guess is no longer allowed to decide that question. Where
            # the log does not determine which call a result answers, and any
            # call still open is one the operator named as handing back the
            # model's own text, the result is recorded as model text. That
            # costs a false alarm when the guess would have been right; the
            # other way costs laundering, which is the failure this project
            # exists to catch. Ambiguity is not evidence.
            open_names = {n for n, count in open_count.items() if count}
            sent = ""                      # arguments of the call this answered
            # False where the log does not say which of several calls this
            # result answers. `could_be` then names the tools it might answer,
            # and their pieces are looked up rather than their text scanned:
            # scanning every candidate's arguments for every result is what
            # made a wide turn without ids quadratic.
            confident = True
            could_be: Set[str] = set()
            own: Optional[_Call] = None     # the call this answered, where known
            unplaced = False                # a result matched to no call at all
            # False where the name is the reader's placeholder or a guess between
            # several tools: a confirmation by name must not reach that warning.
            name_known = bool(named_itself)
            tool, expected, matched = named_itself, "", False

            if cid and cid in tool_names and cid not in reused_ids:
                # An id we have seen names its call outright, whichever turn
                # declared it - unless the same id was declared for two
                # different tools, in which case it names nothing and must not
                # be allowed to claim it does.  If that call is still open here
                # it is answered; if it is not, this result belongs to an
                # earlier turn and must take no slot from this one.
                tool = tool or tool_names[cid]
                own = calls_by_id.get(cid)
                sent = own.args if own is not None else ""
                name_known = name_known or (own is not None and own.named)
                entry = by_id.get(cid)
                if entry is not None and not entry.done:
                    expected, matched = entry.name, True
                    # The result's own name wins over the call's: a gateway
                    # that renames a tool between call and result is saying
                    # what answered, and that is the more direct evidence.
                    tool = named_itself or entry.name
                    entry.done = True
                    open_count[entry.name] -= 1
                    open_with_id -= 1
                if not matched:
                    unmatched.append(f"tool[{k}] (id {cid})")
            elif cid and open_with_id:
                # The open calls are keyed by id and this one matches none of
                # them: it answers a call this turn did not declare, so it
                # takes no slot and must not shift the results that follow.
                # Where the open calls carry no ids at all the id says nothing
                # about them, and the fall-through below applies instead.
                unmatched.append(f"tool[{k}] (id {cid})")
                unplaced = True
            elif named_itself:
                # A name does not tell two open calls of the same tool apart,
                # and their results may come back in either order: the result
                # is weighed against every open call bearing its name.
                queue = by_name.get(named_itself) or []
                i = name_cursor.get(named_itself, 0)
                while i < len(queue) and queue[i].done:
                    i += 1
                name_cursor[named_itself] = i
                if i < len(queue):
                    entry = queue[i]
                    expected, matched = entry.name, True
                    if open_count[named_itself] == 1:
                        own, sent = entry, entry.args
                    else:
                        confident, could_be = False, {named_itself}
                    entry.done = True
                    open_count[named_itself] -= 1
                    if entry.cid:
                        open_with_id -= 1
                if not matched:
                    unmatched.append(f"tool[{k}] ({named_itself})")
                    unplaced = True
            elif open_names:
                while open_calls[head].done:
                    head += 1
                entry = open_calls[head]
                expected = tool = entry.name
                entry.done = True
                open_count[entry.name] -= 1
                if entry.cid:
                    open_with_id -= 1
                # Doubt is contagious within a batch. Once one result has been
                # placed by position alone, the calls left in the queue are not
                # known to be the ones still unanswered - so a later result
                # that looks unambiguous ("only `search` is left") is not. That
                # is how the very first interleaving this rule was written for
                # still recorded an echo as evidence: the wrong guess made the
                # next guess look certain.
                candidates = open_names | doubt
                matched = len(candidates) == 1
                # Placed by position, the result may answer any call of this
                # turn still in question - so it is weighed against all of them.
                if sum(batch_count.get(n, 0) for n in candidates) == 1:
                    own, sent = entry, entry.args
                else:
                    confident, could_be = False, set(candidates)
                name_known = matched and entry.named
                if not matched:
                    doubt |= candidates
                guessed.append(f"tool[{k}] -> {tool}"
                               + ("" if matched else " (by position only)"))
            else:
                guessed.append(f"tool[{k}] -> unknown")
                unplaced = True

            tool = tool or "tool"
            # Uncertain, and one of the calls it might have answered echoes the
            # model: the file does not say which, so it is not evidence.
            # Everything this result might have answered. Where nothing is
            # open and nothing is in doubt, it could have been any tool in the
            # run - which is only a question at all if some of them echo.
            pool = (open_names | doubt) or set(echo)
            # A result's own name settles what it is - unless this batch
            # declared calls and none of them bears that name, in which case
            # the log contradicts itself and the name settles nothing.
            name_trusted = bool(named_itself) and (matched or not open_names)
            ambiguous = not matched and not name_trusted and bool(pool & echo)
            if not content.strip():
                skipped_empty.append(f"tool[{k}] ({tool})")
                continue
            # Asked BEFORE the cut: the echoed value is the last line, and
            # truncating to a prompt-size knob would hide it behind a
            # mid-document line - a root recorded because a limit was low.
            handed_back = _hands_back_what_it_was_given(content, sent)
            if not handed_back and could_be:
                # Placement uncertain: weighed against every call it might
                # answer, by lookup. Such a demotion is never elected the answer.
                handed_back = _stands_among(
                    content, [batch_pieces_by_name.get(n, frozenset()) for n in could_be])
            # Also asked before the cut, for the same reason. Only for a result
            # that stays evidence: a warning is a report, never a demotion.
            warning: Optional[Tuple[str, str]] = None      # (kind, the line)
            # A tool declared external, known by its own name, gets no warning:
            # the operator vouched for it. `handed_back` above was decided
            # before this and is not touched by the declaration.
            vouched = name_known and tool in external
            if not handed_back and tool not in echo and not ambiguous and not vouched:
                if unplaced:
                    warning = ("unmatched", _matched_line(content))
                else:
                    mine = own.pieces if own is not None else frozenset()
                    for piece in _echo_candidates(content):
                        if piece in mine:
                            continue        # its own call; the rule above decided
                        if not could_be and piece in batch_pieces:
                            warning = ("same_turn", piece)
                            break
                        if piece in earlier_pieces:
                            warning = ("earlier_turn", piece)
                            break
            if len(content) > max_tool_chars:
                content = content[:max_tool_chars]
                cut = True
            else:
                cut = False
            aid = f"t{k}"
            if tool in echo or ambiguous or handed_back:
                kind = "intermediate"       # the model's own words handed back
                if handed_back and tool not in echo:
                    echoed_back.append(f"tool[{k}] ({tool}): "
                                       + _short(_matched_line(content)))
                if ambiguous:
                    unresolved.append(
                        f"tool[{k}] could have answered any of "
                        + ", ".join(sorted(pool | {tool}))
                        + "; read as the model's own text because one of them "
                          "hands the model's words back")
            elif tool in verbatim:
                kind = "document"           # external text stored as fetched
            else:
                kind = "tool_result"
            if warning is not None:
                kind_of, line = warning
                # Addressed to the person's file: the message's position there,
                # counted from 0, and the call id, which a search finds and a
                # reformatting does not move. `result` stays the reading's own
                # label, the one the artifact id carries.
                file_index = message.get("_file_index", k)
                call_id = cid or (own.cid if own is not None else "")
                if name_known:
                    who = tool
                elif could_be and not matched:
                    who = " or ".join(sorted(could_be)) + "?"
                else:
                    who = f"{tool}: the log gives no name"
                where = f"tool[{file_index}] ({who})" + (f" call {call_id}" if call_id else "")
                earlier_echoes.append(f"{where}, {_WARNING_KINDS[kind_of]}: {_short(line)}")
                earlier_echo_details.append({
                    "result": f"tool[{k}]", "tool": tool if name_known else "",
                    "line": _short(line), "message": file_index, "call_id": call_id,
                    "kind": kind_of})
            artifacts.append({"artifact_id": aid, "kind": kind,
                              "title": tool, "content": content})
            if cut:
                truncated.append(aid)
            if ambiguous:
                unsure_ids.add(aid)
            if handed_back and not confident and tool not in echo:
                unconfident_ids.add(aid)
            if kind == "intermediate":
                # Model text is produced by the turn that wrote it, not by the
                # tool step: it must be funded like anything else the model said.
                steps.append({"step_id": aid, "kind": "generate",
                              "inputs": list(seen), "outputs": [aid]})
                seen.append(aid)
                model_text.append(len(artifacts) - 1)
            else:
                pending_tools.append(aid)
            continue

        # An unknown role is still text that was in the conversation; recording
        # it as a root would let a claim stop on it, so it is left out and said.
        dropped.append(f"{role or 'unknown'}[{k}]")

    flush_tools()

    # The last thing the model said in words is what the user saw.
    # An artifact that is only on the model's side because the reading could
    # not place it is not evidence that the user saw it.
    # The same holds for a result demoted against several calls the log could
    # not tell apart: it is the model's text, but not known to be the text the
    # user saw. Only a demotion against the one call a result answered - or a
    # tool the operator declared - may be elected.
    if model_text and artifacts[model_text[-1]]["artifact_id"] in (unsure_ids | unconfident_ids):
        # The last thing on the model's side is only there because the reading
        # could not place it. Promoting it would assert the user saw it;
        # promoting the one before it would assert the user saw THAT. Neither
        # is known, so the trace records no answer and `check-trace` reports
        # `no_final_answer`, which is the true state of this reading.
        no_answer = ("the last model-side artifact is one the reading could not "
                     "place, so this trace records no final answer: nothing here "
                     "is known to be what the user saw")
        notes = list(notes) + [no_answer]
        model_text = []
    # A tool result read as the model's own words is eligible to be the answer,
    # exactly like one the operator declared with --tool-returns-model-text. A
    # review pass had this heuristic's results barred from the election and the
    # declared ones kept, and the asymmetry was worse than either rule alone:
    # declaring the tool truthfully then turned "records no answer, exit 1" into
    # "exit 0". A flag that makes the audit stricter must never be what turns a
    # gate green, so the two paths are treated the same. The residual risk is
    # stated in the module docstring: a wrongly demoted last tool result becomes
    # the answer, which is why the rule is reported rather than applied silently.
    # A log that stops on a tool's reply does not say what the user saw. The
    # model's last message before it is a thought, not an answer, and electing
    # it would make the trace assert something the file does not contain -
    # while the text the user most likely saw sits in the tool result. Refusing
    # to guess is the honest reading, and `check-trace` then reports
    # `no_final_answer` rather than auditing the wrong artifact.
    if artifacts and model_text and artifacts[-1]["kind"] in ("tool_result", "document") \
            and artifacts[-1]["artifact_id"].startswith("t"):
        notes = list(notes) + [
            "this log stops on a tool's reply, so it does not record what the "
            "user saw: the model's last message before it is a step, not an "
            "answer, and this reading does not promote it to one"]
        model_text = []
    if model_text:
        last = artifacts[model_text[-1]]
        last["kind"] = "final_answer"
        answer_id = last["artifact_id"]
        for step in steps:
            if step["outputs"] == [answer_id]:
                step["kind"] = "answer"

    meta = {
        "source": "openai-chat",
        "file": name,
        "messages": len(messages),
        "truncated": truncated,
        "dropped_messages": dropped,
        "skipped_empty": skipped_empty,
        "max_tool_chars": max_tool_chars,
        "model_text_tools": sorted(echo),
        "verbatim_tools": sorted(verbatim),
        "external_tools": sorted(external),
        "guessed_tool_names": guessed,
        "unmatched_tool_results": unmatched,
        "unresolved_tool_results": unresolved,
        "echoed_back_tool_results": echoed_back,
        "echoes_from_earlier_turns": earlier_echoes,
        "echo_warning_details": earlier_echo_details,
        "notes": notes,
    }
    return {"artifacts": artifacts, "steps": steps, "_meta": meta}
