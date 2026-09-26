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
result that IS a value the call it answered carried - the whole reply, one of
its lines, the single value of a reply that is JSON, a literal that is the
whole reply, or the text after a `label: ` where that label and value are the
whole reply - is read as the model's own text even when the tool was declared
verbatim: the model wrote that value, whatever the tool usually does. The
comparison is exact, after whitespace is collapsed, after escapes are undone,
and with trailing punctuation trimmed one mark at a time. There is no
proportion in it and no privileged line, so there is nothing to shift: the
sixth review walked the old rule - "the last line, or more than half of the
reply" - with three characters, a newline dropped into the middle of an echo
and a one-character line after it, and took it from 216 fires on AgentHallu to
0. Every override is named in `_meta.echoed_back_tool_results`.

**Anything short of that is a note, not a demotion.** One question, one
number, and the same pair on every path: is at least `WARN_SHARE` of the reply,
counted in letters and digits, covered by the text of a call - the one it
answered, or any call of an earlier turn, or another call of this turn. Both
sides are read as words, so a newline dropped into the middle of an echo
changes nothing. Recorded in `_meta.echo_warning_details` with the kind that
says which call it was weighed against. (The field names are older than the
decision below and stay, so traces written before it read the same.)

**A note is a reason to look, not a finding.** Review 16 drew 20 of these
records at random on AgentHallu and read them by hand: 6 were the model's own
text handed back, 7 a tool doing honest work (a number computed under a label
the model wrote, a search repeating its query), 7 disputable (a browser status
line, a record of an order the model placed). The labels are kept outside this
repository. So the records never move an exit code and ask nobody to confirm
anything: `check-trace` and `audit` list them under the report and in `--json`,
with that count, and the person decides. Until round 18 they held the exit at
1 until each tool was confirmed by name; round 18 removed them; round 19
brought the same detection back as a note.

Every comparison on the echo path is made on a cleaned copy of both sides:
every kind of space made a plain one, zero-width marks dropped. A zero-width
space after a value and a non-breaking space in a JSON reply each emptied a rule
before. Case is kept on both paths, so a value read back in another case is
not reported: folding it cost 61 real results on the demotion and 6 warnings
with no real echo among them on the warnings (see the note after `_clean`).

**What this reading is for, and what it is not.** It catches an agent quoting
itself by accident - the model does not know it is checked and is not trying to
get past anything. It is not a defence against someone who knows the rule:
text added to a reply lowers the share without removing a character of the
model's words, and a line of junk shorter than the echo is enough. The README
gives the measured price.

One question coverage cannot ask is whether a short reply is a value an earlier
call carried: `answer = "B"` stored in one turn and printed in the next is four
characters, below any run worth counting. So a reply that IS, whole, one of the
plain values of an earlier call - its JSON values, its quoted literals, its
lines, the bare token of a `name = value` line of code, but not its other bare
word atoms - is reported too, with or without quotes around the reply. That has
no threshold in it either.

A reply MAX_STARTS kept from being weighed to the end is not passed: it is
weighed again without the limit, and where even that is too costly it is
reported with the kind `unchecked`.

**A tool that hands back text from ANOTHER call is noted, not demoted.**
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
`model_text_tools`: that demotes its results, which a note never does.

**A tool that hands back a whole MESSAGE the model wrote is noted too.** A
memory or history tool returning what the assistant said a turn ago: where the
piece that holds most of the reply - the reply, a line, a `label: ` value, a
JSON value - IS an earlier assistant message, the kind is `model_message`. Only
whole messages: two readings by share were measured on AgentHallu and noted
honest work every time (see `message_values` in `to_trace`).

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

import bisect
import functools
import json
import re
import unicodedata
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

DEFAULT_MAX_TOOL_CHARS = 20_000

#: Roles whose content entered the run from outside the assistant.
_ROOT_ROLES = frozenset({"system", "developer", "user"})

#: Roles this reader understands. Anything else is recorded as dropped rather
#: than guessed at: a role we cannot place is a role we cannot say is a root.
_KNOWN_ROLES = _ROOT_ROLES | {"assistant", "tool", "function"}



#: Tool-call arguments nested deeper than this are refused: `to_trace` raises
#: ValueError, and the commands exit 2. The reader's own rule, so that the answer
#: does not depend on the Python it runs on. Before it, where the interpreter gave
#: up decided: arguments nested 1,500 deep were read on 3.10 (its JSON parser
#: fails first, and the reader took them as text) and refused on 3.12 (its parser
#: goes deeper, and the recursive walk after it failed) - round 22. Well under the
#: ~1,000 levels where any supported Python fails, with room left for the stack
#: of whatever calls `to_trace`.
MAX_ARGUMENT_NESTING = 256


def _nesting(text: str) -> int:
    """How deep `[` and `{` nest in `text`, outside double-quoted strings.

    One pass and no recursion, so it answers for any depth on any Python. Not a
    parser: it does not ask whether the text is JSON. Code arguments are counted
    the same way; their single-quoted strings are not recognised, so brackets
    inside those count."""
    depth = deepest = 0
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > deepest:
                deepest = depth
        elif ch in "]}" and depth:
            depth -= 1
    return deepest


def _args_text(raw) -> str:
    """The arguments a tool call carried, as text. OpenAI sends them as a JSON
    string, the Anthropic shape as an object; both are compared as text.

    Raises ValueError where they nest deeper than MAX_ARGUMENT_NESTING - before
    anything parses or walks them."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        text = raw
    else:
        try:
            text = json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(raw)
    if _nesting(text) > MAX_ARGUMENT_NESTING:
        raise ValueError(f"a tool call's arguments are nested more deeply than this reader "
                         f"can follow (more than {MAX_ARGUMENT_NESTING} levels)")
    return text




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


def _matched_line(result: str) -> str:
    """The line the echo test looked at - the last non-empty one."""
    lines = [ln.strip() for ln in result.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _short(text: str, limit: int = 80) -> str:
    """A quotation short enough for a terminal, long enough to check."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"

def _spellings(text: str) -> Tuple[str, str, str]:
    """`text` as written, unescaped once, and unescaped twice. Twice, because
    two layers happen: the model's own code escapes its quotes and the JSON
    layer escapes them again. A third layer has no example behind it."""
    # Decoding is a pass over every character, and most text carries no escape
    # at all: a word of someone's code, a line of prose. Without this guard the
    # three spellings of every word of a 21 KB argument are decoded twice.
    if "\\" not in text:
        return text, text, text
    once = _unescape(text)
    return text, once, _unescape(once)


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


#: Marks that change how a string compares and not how it reads: zero-width
#: characters are dropped, and every other kind of space becomes a plain one.
#: A non-breaking space in a JSON reply stopped the demotion outright - JSON
#: does not allow it between tokens, so the reply no longer parsed - and a
#: zero-width space after a value emptied the rule with no threshold in it.
_CLEAN = {**dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad")),
          **{ord(c): " " for c in "        "
                                  "       　"}}


def _clean(text: str) -> str:
    """`text` with invisible marks dropped, every space a plain one, and in NFC.
    For COMPARISON ONLY: a recorded artifact is never touched by this.

    NFC because the verifier compares in NFC (normalize.py) and this reader
    compared code points: a Vietnamese or Korean note handed back decomposed -
    as macOS keeps file names, as some stores keep text - shared no word with
    the call that wrote it. It was neither demoted nor noted, and a claim
    quoting it balanced, because to the audit it was the same text (review 20,
    2.4b). A string already in NFC comes back unchanged."""
    return unicodedata.normalize("NFC", text.translate(_CLEAN))


#: Case is NOT folded, on either path, and that leaves a gap on purpose: a value
#: read back in another case - `canberra` after `Canberra` - is not reported.
#: Both ways were measured on AgentHallu. Folding case in the demotion demoted
#: 61 more results, and each one read was a tool doing real work: a ticker
#: lookup answering `Zeta Corp` with `ZETA`, a browser naming the page it
#: opened. Folding it in the warnings added 6 warnings on 2 trajectories, and
#: of the six, read by hand, none was a real echo. This reading is for an agent
#: quoting itself by accident, and accidental self-quoting is letter for letter.


_QUOTE_PAIRS = {'"': '"', "'": "'", "`": "`", "“": "”",
                "‘": "’", "«": "»"}


def _unquoted(text: str) -> str:
    """`text` with the pairs of quotes around the whole of it taken off:
    `"Canberra"` states the same value as `Canberra`."""
    s = _clean(text).strip()
    while len(s) >= 2 and _QUOTE_PAIRS.get(s[0]) == s[-1]:
        s = s[1:-1].strip()
    return s


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
    texts = [_clean(t) for t in texts]
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
                        found.add(_norm(_clean(_unescape(literal))))
        found.update(_WORD.findall(text))
    return frozenset(found)


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
    # Cleaned; case is kept (see the note after `_clean`).
    for text in [_clean(t) for t in texts]:
        total = _alnum(text)
        if not total:
            continue

        def add(piece: str, total: int = total) -> None:
            if 2 * len(piece) <= total:
                return              # too short to be most of the result
            # Cleaned again: a JSON value is decoded from the text, and an
            # escape can decode to a decomposed letter.
            piece = _norm(_clean(piece))
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
    "answering_call": "text from the call it answered, short of the whole reply",
    "unchecked": "a reply the reading could not finish weighing against the model's "
                 "calls, so it may be the model's own text",
    "model_message": "a whole message the model wrote earlier",
}


#: How much of a reply must stand in the text of a call before the reading says
#: so out loud. The ONE number in this file's echo path, on BOTH paths, and it
#: moves only NOTES - a demotion never consults it, and a note never moves an
#: exit code, so no setting of it can turn the model's own words into evidence
#: or fail a run.
#:
#: Chosen in round 8 by reading two hand-drawn samples of 20 at 30% and at 50%;
#: those samples were not kept, and they did not tell the two settings apart.
#: What this setting is worth today is review 16's sample at 50%: 6 of 20 real
#: echoes (see the module docstring). Changing it without a larger sample is
#: guessing.
WARN_SHARE = 0.50


def _forms(text: str) -> List[str]:
    """`text` as a value might be written: each of its spellings, cleaned,
    whitespace collapsed, and its trailing punctuation trimmed ONE MARK AT A
    TIME. Case is kept (see the note after `_clean`).

    One mark at a time because the run used to be stripped whole, which put the
    form with no punctuation and the form with two into the set but never the
    one with one - and one is what the arguments carry when a sentence that
    already ended in a period comes back with another added."""
    out: List[str] = []
    for spelling in _spellings(text):
        value = _norm(_clean(spelling))
        while True:
            if value and any(ch.isalnum() for ch in value):
                out.append(value)
            if value and value[-1] in _TRAILING:
                value = value[:-1]
            else:
                break
    return out


@functools.lru_cache(maxsize=512)
def _arg_values(args: str) -> frozenset:
    """The VALUES one call carried - not its words.

    Words would make `the` a value and demote every English sentence. Values
    are what a tool is handed: each JSON value, each quoted literal, each line,
    and - for arguments that are code rather than JSON - each bare atom, since
    `final_answer(12)` hands the answer in unquoted and nothing else finds it.
    """
    if not args:
        return frozenset()
    found: Set[str] = set()
    try:
        parsed = json.loads(args)
    except (TypeError, ValueError, RecursionError):
        parsed = None
    texts = list(_values(parsed)) if parsed is not None else []
    if not texts:
        texts = [args]
    for value in texts:
        found.update(_forms(value))
    # Only the values, never the raw argument text where it parsed: its KEYS
    # are the caller's vocabulary, not anything the model stated, and reading
    # them as values made a reply of `x` an echo of `{"cols": {"x": 1}}`.
    scan = texts + [_unescape(t) for t in texts if "\\" in t]
    for text in scan:
        for pattern in (_LITERAL_DQ, _LITERAL_SQ):
            for match in pattern.finditer(text):
                found.update(_forms(match.group(1)))
        for line in text.splitlines():
            found.update(_forms(line.strip()))
        for match in _WORD.finditer(text):
            found.update(_forms(match.group(0)))
    return frozenset(found)


def _reply_texts(result: str) -> List[str]:
    """The reply as written, and with its escapes undone - a note read back out
    of a JSON store arrives escaped, and the value is only there once it is
    decoded. Long replies are not decoded: decoding is a pass over every
    character, and a long page that is ALSO escaped JSON is not the shape of a
    value handed back.

    Cleaned first, because its shape is read here - whether it is JSON, whether
    it is one `label: value` line - and a non-breaking space breaks both."""
    result = _clean(result)
    if "\\" in result and len(result) <= UNESCAPE_RESULT_CHARS:
        return [result, _unescape(result)]
    return [result]


def _reply_values(result: str, whole_only: bool) -> Iterator[str]:
    """What the reply offers as a value.

    `whole_only` is the demotion's question, and it is deliberately narrow: the
    reply itself, a quoted literal that IS the whole reply (a REPL printing a
    repr), and - where the reply is JSON carrying exactly ONE value - that
    value. One value, because "the reply holds a single value and it is the one
    the call was given" is a statement about the reply's shape with no number
    in it, and it is what separates a note store answering
    `{"saved": true, "text": <the model's paragraph>}` from a trading API
    answering `{"order_id": 12446, "order_type": "Buy", "price": 320.0}`, where
    `Buy` and `320.0` came from the call and the order id did not. Measured on
    AgentHallu: 44 demotions beyond the whole reply, against 448 when every
    line and every labelled value may demote.

    Otherwise - the warning's question - every line, the text after a
    `label: `, every quoted literal and every JSON value."""
    for text in _reply_texts(result):
        yield from _forms(text)
        stripped = text.strip()
        if whole_only:
            for pattern in (_LITERAL_DQ, _LITERAL_SQ):
                match = pattern.fullmatch(stripped)
                if match:
                    yield from _forms(match.group(1))
            if stripped[:1] in ("{", "["):
                values = _json_values(stripped)
                if len(values) == 1:
                    yield from _forms(values[0])
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            for line in lines:
                yield from _forms(line)
            # A `label: value` split, but only where the reply is that one line.
            # `Saved note: <the model's paragraph>` is a value handed back;
            # `Search results for: <query>` followed by the results is a header
            # over a reply that also carries its own text, and demoting it
            # would throw the results away. The count of lines is the only
            # thing that separates them, and it is a shape, not a threshold.
            if len(lines) == 1:
                _label, sep, rest = lines[0].partition(": ")
                if sep:
                    yield from _forms(rest)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            yield from _forms(line)
            _label, sep, rest = line.partition(": ")
            if sep:
                yield from _forms(rest)
        for pattern in (_LITERAL_DQ, _LITERAL_SQ):
            for match in pattern.finditer(text):
                yield from _forms(match.group(1))
        if stripped[:1] in ("{", "[", '"'):
            for value in _json_values(stripped):
                yield from _forms(value)


@functools.lru_cache(maxsize=256)
def _json_values(text: str) -> Tuple[str, ...]:
    """The values of a reply that is JSON, or () where it is not. Booleans and
    nulls are not values here: `{"saved": true, "text": ...}` carries one."""
    try:
        return tuple(_values(json.loads(text)))
    except (TypeError, ValueError, RecursionError):
        return ()


def _is_a_value_of(result: str, sent: str, whole_only: bool) -> str:
    """The value of the call's arguments that the reply hands back, or "".

    Does the reply - or something in it - stand in the call's arguments as a
    whole value? A one-character match is not a value: a stray trailing `e` at
    the end of a page once matched the letter `e` in the model's own writing
    and demoted the only real evidence in that run. A reply that IS one
    character is a different thing and stays caught. Structural, not a
    threshold: there is no other number it could be."""
    values = _arg_values(sent)
    if not values:
        return ""
    whole = _norm(_clean(result))
    for form in _reply_values(result, whole_only):
        if form in values and (len(form) > 1 or form == whole):
            return form
    return ""


def _call_texts(args: str) -> List[str]:
    """One call's arguments in each spelling a reply could match: as written,
    its quoted literals, and its JSON values."""
    texts = [args]
    literals = [m.group(1) for pattern in (_LITERAL_DQ, _LITERAL_SQ)
                for m in pattern.finditer(args) if m.group(1).strip()]
    if literals:
        texts.append("\n".join(literals))
    try:
        parsed = json.loads(args)
    except (TypeError, ValueError, RecursionError):
        parsed = None
    if parsed is not None:
        values = list(_values(parsed))
        if values:
            texts.append("\n".join(values))
    return texts


#: A word that stands in this many places is a word of the language, not a
#: piece of anyone's text, and on the first pass runs are not started from it:
#: without that, one turn of a long CodeAct run costs the square of its length.
#:
#: Skipping a start makes the reading say LESS, and saying less is a silent
#: pass. The ninth review repeated each word of an echo 22 times in a comment,
#: and a reply that was 90% the model's sentence went out with exit 0; on the
#: path that weighs earlier turns the index grows over the whole run, so an
#: agent that kept handing its own note to tools blinded the check after 32
#: turns. So a skip is never the last word. Where the words it could not rule
#: out could still reach WARN_SHARE, the reply is weighed again with no limit,
#: within RECHECK_WORK; where that runs out too, the reading notes the reply
#: `unchecked`: not "no echo here" but "not weighed to the end".
MAX_STARTS = 64

#: How many index positions the exact pass may examine for one reply, and for
#: one whole log. Past either, the reply is reported `unchecked` instead of
#: weighed: a bounded cost, and nothing passed in silence.
RECHECK_WORK = 2_000_000
RECHECK_WORK_TOTAL = 20_000_000

#: Written between two texts in an index so that no run is matched across the
#: join. It cannot occur in a reply: `_words_of` never produces it.
_JOIN = "\x00"


#: Scripts whose ordinary modern writing puts no space between words, so a note
#: in them is one whitespace word however long it is: Thai, Lao, Tibetan,
#: Myanmar, Khmer, Japanese kana, Han, halfwidth katakana. That is the rule for
#: being here, not a list of languages someone thought of: Tibetan was missing
#: (review 16, 2.3) because the list was written down from memory. Korean is
#: written with spaces and is not here. Scripts that meet the rule and are still
#: not here, each untested and so left out rather than added blind: Tai Tham,
#: Tai Le, New Tai Lue, Tai Viet, Balinese, Javanese, Yi, and the Han and kana
#: blocks outside the ranges below (extensions B and later, kana supplements).
_NO_SPACES = re.compile("[\u0e00-\u0eff\u0f00-\u0fff\u1000-\u109f\u1780-\u17ff"
                        "\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf"
                        "\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]")

#: Thai, Lao, Tibetan, Myanmar and Khmer are cut only at marks from outside
#: their own blocks - a quote, a colon, a bracket - and not at their own. The
#: vowel signs above and below a letter, and Tibetan's tsheg (་) between
#: syllables, are not letters to `_WORD`, and cutting there made every note a
#: string of single consonants that stand everywhere in the index.
#:
#: Tibetan first (round 17): on a chat of 100 turns with no echo in it, 1,000
#: Tibetan characters a call, the index ran out of RECHECK_WORK and 39 replies
#: came back `unchecked`; with this, none (tests/test_core_claim_cycles.py). The other
#: four in round 19, measured on a generated chat of 1,000 turns with no echo
#: in it: before, 175 (light) and 863 (heavy) false `unchecked` notes in Thai,
#: 573 and 910 in Lao, 357 and 879 in Khmer, 376 and 882 in Myanmar; after, 0
#: in each. A note in them is still read whole between outside marks, as a
#: Tibetan one is.
_WHOLE_IN_BLOCK = re.compile("[\u0e00-\u0eff\u0f00-\u0fff\u1000-\u109f\u1780-\u17ff]")
_WHOLE_IN_BLOCK_WORD = re.compile(
    "(?:[^\\W_]|[\u0e00-\u0eff\u0f00-\u0fff\u1000-\u109f\u1780-\u17ff])+")


def _words_of(text: str) -> List[str]:
    """`text` as the words the share is counted in: split at whitespace, and a
    word in a script written without spaces split again at every mark that is
    not a letter or a digit.

    In Chinese and Japanese a whole note is one whitespace word, so the quote of
    a JSON value, a fullwidth `：` after a label or 「」 around it made that one
    word match nothing and the share fell to 0: check-trace exited 0 on the
    model's own note.

    Why only there. This is a second way of cutting words, beside whitespace,
    and the two wider ways were tried first and priced on AgentHallu against a
    limit named before the measurement (round 15):
      * cutting at marks in EVERY script also changed English: a JSON key
        `current_time` became the words `current time`, a file name in a `mv`
        status line matched the call that named it, and the warnings it added
        went past the limit, fewer than half of those read by hand real echoes
        (tests/test_exit_codes_paths_and_overwrite.py keeps both shapes quiet);
      * one character per word in these scripts: on a long Chinese chat with
        no echo in it, the index holds every character thousands of times, the
        exact pass runs out of RECHECK_WORK and replies come back `unchecked`,
        exit 1 (tests/test_exit_codes_paths_and_overwrite.py, 100 turns).
    AgentHallu has no note written in these scripts, so the corpus priced this
    and did not test it; tests/test_model_text_is_never_silent.py and
    test_exit_codes_paths_and_overwrite.py do.

    What it still cannot see, and nothing here reports: a note glued to other
    letters with no mark between them - `已保存` written straight onto it - a
    note set inside a longer clause of the reply, and a clause read back with
    one character changed. Each is a word that matches nothing, where in
    English only the changed word would."""
    words: List[str] = []
    for word in _clean(text).split():
        if _NO_SPACES.search(word):
            words.extend((_WHOLE_IN_BLOCK_WORD if _WHOLE_IN_BLOCK.search(word)
                          else _WORD).findall(word))
        else:
            words.append(word)
    return words


class _Words:
    """A growing word sequence with an index of where each word stands, so a
    reply can be weighed against every call of a run without scanning them."""

    __slots__ = ("words", "where")

    def __init__(self) -> None:
        self.words: List[str] = []
        self.where: Dict[str, List[int]] = {}

    def __bool__(self) -> bool:
        return bool(self.words)

    def add(self, text: str) -> None:
        base = len(self.words)
        parts = _words_of(text)
        for i, word in enumerate(parts):
            self.where.setdefault(word, []).append(base + i)
        self.words.extend(parts)
        self.words.append(_JOIN)

    def add_call(self, args: str) -> None:
        for text in _call_texts(args):
            self.add(text)

    def absorb(self, other: "_Words") -> None:
        base = len(self.words)
        for word, places in other.where.items():
            self.where.setdefault(word, []).extend(p + base for p in places)
        self.words.extend(other.words)


@functools.lru_cache(maxsize=512)
def _call_values(args: str) -> frozenset:
    """The values one call carried, WITHOUT its bare word atoms.

    The demotion reaches for atoms, because `final_answer(12)` hands the answer
    in unquoted and the log ties that call to that result. Across calls nothing
    ties them, and an atom match there is the noise the eighth round's sample
    condemned: a reply containing the word `False`, a query term, a file name.
    So this asks only about values the call plainly carried - its JSON values,
    its quoted literals, its lines."""
    if not args:
        return frozenset()
    found: Set[str] = set()
    try:
        parsed = json.loads(args)
    except (TypeError, ValueError, RecursionError):
        parsed = None
    texts = list(_values(parsed)) if parsed is not None else [args]
    for value in texts:
        found.update(_forms(value))
    for text in texts + [_unescape(t) for t in texts if "\\" in t]:
        for pattern in (_LITERAL_DQ, _LITERAL_SQ):
            for match in pattern.finditer(text):
                found.update(_forms(match.group(1)))
        for line in text.splitlines():
            found.update(_forms(line.strip()))
            # `answer = B` states what `answer` IS, exactly as `answer = "B"`
            # does - and the literal pattern only reads the quoted one, so a
            # model that left the quotes off was never checked.
            assigned = _ASSIGNED_ATOM.match(line)
            if assigned:
                found.update(_forms(assigned.group(1)))
    return frozenset(found)


#: A line of code that assigns one bare token - a word, a number, `True` - and
#: nothing else: `answer = B`, `answer=0`, `total = 4.6  # tonnes`. Not
#: `results = compute(dataset)`, which hands `dataset` to a function rather
#: than stating anything is `dataset`, and not a comparison `x == 5`.
_ASSIGNED_ATOM = re.compile(r"^\s*[A-Za-z_][\w.]*\s*=\s*([\w.+-]+)\s*(?:;\s*)?(?:#.*)?$")


@functools.lru_cache(maxsize=512)
def _arg_words(args: str) -> _Words:
    """One call's arguments, indexed. Cached: the demotion and the warning ask
    about the same call, and a wide turn asks about every one of them."""
    index = _Words()
    for text in _call_texts(args):
        index.add(text)
    return index


def _tile(reply: List[str], index: _Words, limit: Optional[int],
          allowance: Optional[int]) -> Tuple[Optional[int], Tuple[int, int], bool, int]:
    """Tile the reply's words left to right by maximal runs found in `index`.

    Returns (letters and digits covered, the longest counted run as a
    (start, end) word span, whether a start was skipped for standing in more
    than `limit` places, positions examined). Covered is None where `allowance`
    ran out before the tiling ended - the caller must then say it could not
    check, not that it found nothing."""
    words, where = index.words, index.where
    covered = work = 0
    best_run, best = 0, (0, 0)
    skipped = False
    i = 0
    while i < len(reply):
        live = where.get(reply[i])
        if not live:
            i += 1
            continue
        if limit is not None and len(live) > limit:
            skipped = True
            i += 1
            continue
        run = 1
        while True:
            if allowance is not None:
                work += len(live)
                if work > allowance:
                    return None, best, skipped, work
            further = [p for p in live
                       if p + run < len(words) and i + run < len(reply)
                       and words[p + run] == reply[i + run]]
            if not further:
                break
            live, run = further, run + 1
        matched = _alnum("".join(reply[i:i + run]))
        if matched >= ECHO_MIN_CHARS:
            covered += matched
            if matched > best_run:
                best_run, best = matched, (i, i + run)
        i += run
    return covered, best, skipped, work


class _Budget:
    """What is left of RECHECK_WORK_TOTAL for one reading of one log."""

    __slots__ = ("left",)

    def __init__(self) -> None:
        self.left = RECHECK_WORK_TOTAL


def _share_in(result: str, index: _Words,
              budget: Optional[_Budget] = None) -> Tuple[float, str, bool]:
    """How much of the reply, in letters and digits, stands in this text - the
    longest run that did, so a warning can quote it - and whether the reading
    could NOT finish weighing it.

    Both sides are read as words, cleaned, so every run of whitespace is one
    space and a newline dropped into the middle of an echo changes nothing: that is how three characters used to empty this rule,
    on both paths. The reply is tiled left to right by maximal runs; a run
    shorter than ECHO_MIN_CHARS is not counted, because two texts in the same
    language share short strings. Greedy, so it can only UNDER-count.

    First with MAX_STARTS. If that skipped a start and fell short, it is asked
    whether the skip could have mattered: only a word that stands in the index
    at all can be covered, so where those words cannot reach WARN_SHARE the
    answer stands. Otherwise the reply is tiled again with no limit, within
    RECHECK_WORK and what is left of `budget`; if that runs out, the third
    value is True and the caller reports the reply `unchecked`.

    The reply is weighed in each spelling the demotion reads it in -
    `_reply_texts`: as written, and with its escapes undone - and the larger
    share stands. One measure on both paths: a note read back as
    `json.dumps` writes it by default arrives as `\\uXXXX`, the demotion
    decoded it and this path did not, so the same Russian note that warned
    when written plainly passed with exit 0."""
    best: Tuple[float, str, bool] = (0.0, "", False)
    unsure = False
    for text in _reply_texts(result):
        share, run, unchecked = _share_of_spelling(text, index, budget)
        if share >= WARN_SHARE:
            return share, run, False
        unsure = unsure or unchecked
        if share > best[0] or not best[1]:
            best = (share, run, False)
    return best[0], best[1], unsure


def _share_of_spelling(result: str, index: _Words,
                       budget: Optional[_Budget]) -> Tuple[float, str, bool]:
    """`_share_in` for one spelling of the reply."""
    reply = _words_of(result)
    total = _alnum("".join(reply))
    if not total or not index:
        return 0.0, "", False

    def quoted(span: Tuple[int, int]) -> str:
        return " ".join(reply[span[0]:span[1]])

    covered, best, skipped, _work = _tile(reply, index, MAX_STARTS, None)
    if not skipped or covered / total >= WARN_SHARE:
        return covered / total, quoted(best), False
    reachable = sum(_alnum(w) for w in reply if w in index.where)
    if reachable / total < WARN_SHARE:
        return covered / total, quoted(best), False
    budget = budget or _Budget()
    allowance = max(0, min(RECHECK_WORK, budget.left))
    exact, exact_best, _skipped, work = _tile(reply, index, None, allowance)
    budget.left -= min(work, allowance)
    if exact is None:
        return covered / total, quoted(best), True
    return exact / total, quoted(exact_best), False


def _echo_share(result: str, sent: str,
                budget: Optional[_Budget] = None) -> Tuple[float, str, bool]:
    """The share of the reply that stands in the arguments of the call it
    answered, as `_share_in` weighs it."""
    if not sent:
        return 0.0, "", False
    return _share_in(result, _arg_words(sent), budget)


def _hands_back_what_it_was_given(result: str, sent: str) -> bool:
    """Is the whole reply a value the call it answers carried?

    `sent` is the arguments of that call, and empty where the log does not say
    which of several calls a result answers.

    This is the demotion, and it is the only place the reading moves an
    artifact out of the root set on its own. It asks one question with no
    number in it: the reply, in any of its spellings and with its trailing
    punctuation trimmed one mark at a time, IS one of the values the call was
    given. Nothing about halves, nothing about the last line, nothing about
    whether a match holds a space - the three settings the sixth review walked
    through with a newline and a one-character line.

    What it costs, counted afresh on AgentHallu for 3edc4e0 (the corpus as
    bench/openai_roundtrip.py renders it; tests/test_openai_roundtrip_numbers.py
    pins the figures; bench/echo_coverage.py, which measured the threshold, was
    deleted in 60cd0f9). With the corpus's four echo tools declared it fires 313
    times over 3535 tool results, 177 of them on tools nobody declared. Against
    the reader written for that corpus by hand it is stricter on 157 artifacts
    and laxer on none - on this corpus. That last clause is not decoration:
    "laxer on none" is a measurement of these 693 trajectories, not a property
    of the code, and the sixth review's three-character bypass was exactly a
    laxer case that this corpus does not contain.

    What the notes are worth is stated where their one number is, at
    WARN_SHARE, and in the README.

    Why this rule is allowed to be wrong. It can only move an artifact OUT of
    the root set - from evidence to model text. A mistake makes the audit
    stricter (a claim loses a source it might have had), never laxer (model
    text recorded as a source, which is the failure this library exists to
    catch). That asymmetry is the whole licence for running it without the
    operator declaring anything, so any change that gives it a second effect
    voids the licence until the argument is made again.
    """
    if not sent:
        return False
    return bool(_is_a_value_of(result, sent, whole_only=True))


def _partly_hands_back(result: str, sent: str,
                       budget: Optional[_Budget] = None) -> Tuple[str, bool]:
    """Is enough of the reply covered by the text of the call it answered?

    One question, one number, and the same pair used on the cross-turn path.
    Returns the longest run it matched, or "" - the warning quotes it, so the
    person reading the report sees what the reading saw - and whether the
    reply could not be weighed to the end.

    What used to be here as well: "a value of the call stands anywhere inside a
    longer reply". It was dropped after its warnings were drawn and read: it
    cost 300 of 693 trajectories on its own, and of ten random ones one was a
    real echo (a tweet the model wrote, handed back with an id), one borderline,
    and eight a tool doing its job - a search repeating its own query, an
    invoice looked up by the id it was given, a browser naming the URL it
    opened. It was first written down as none of ten; one is the measured
    figure. A warning nobody can act on is not a safety property; it is noise
    that teaches an operator to pass the flag without looking."""
    if not sent:
        return "", False
    share, run, unchecked = _echo_share(result, sent, budget)
    return (run if share >= WARN_SHARE else ""), unchecked


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


#: The name the reader puts in where the log gives a tool none. It is this
#: module's own word, not anything the operator can have meant, so it can be
#: not be declared.
PLACEHOLDER = "tool"


def _key(name: str) -> str:
    """A tool name as it is compared: case folded, outer space trimmed. Declaring
    `Read_File` for a log that says `read_file` used to do nothing, in silence."""
    return str(name).strip().casefold()


def _declared(names: Optional[Iterable[str]]) -> Dict[str, str]:
    """Declared names as {compared key: the spelling the operator typed}. The
    placeholder is dropped: nobody can declare the word this reader made up."""
    out: Dict[str, str] = {}
    for raw in names or ():
        key = _key(raw)
        if key and key != PLACEHOLDER:
            out.setdefault(key, str(raw))
    return out


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
    # The placeholder is dropped from every declaration below, and that used to
    # happen in silence: on a log whose tool is literally named `tool` the flag
    # changed nothing and nothing said so (review 16, 2.4). It stays refused -
    # this reader files calls by name, and a call the log left unnamed is filed
    # under the same word, so no declaration of it can be kept off those - but
    # the refusal is said out loud.
    refused = sorted({flag for flag, names in (
        ("--tool-returns-model-text", model_text_tools),
        ("--tool-returns-verbatim", verbatim_tools),
        ("--tool-returns-external", external_tools))
        for raw in names or () if _key(raw) == PLACEHOLDER})
    if refused:
        notes = list(notes) + [
            f"{' and '.join(refused)} {PLACEHOLDER}: not applied. `{PLACEHOLDER}` is the "
            f"name this reading gives a call the log left unnamed, so it cannot be "
            f"declared - the declaration would reach every unnamed call. A tool the "
            f"log itself names `{PLACEHOLDER}` cannot be declared either, and "
            f"--require-declared-tools cannot pass while one is in the log"]
    echo = _declared(model_text_tools)
    verbatim = _declared(verbatim_tools)
    external = _declared(external_tools)
    overlap = sorted(echo.keys() & verbatim.keys())
    if overlap:
        raise ValueError(
            f"{sorted(echo[k] for k in overlap)} is named as both a tool that returns "
            f"the model's own text and a tool that returns external text verbatim; it "
            f"cannot be both")
    overlap = sorted(external.keys() & (echo.keys() | verbatim.keys()))
    if overlap:
        raise ValueError(
            f"{sorted(external[k] for k in overlap)} is declared as returning external "
            f"evidence and also as returning the model's own text or text verbatim; it "
            f"cannot be both")
    # Every name the log actually gives a tool, so a declaration that matches
    # none of them can be said out loud instead of doing nothing in silence.
    named_in_log: Set[str] = set()

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
    # The candidate set as a result sees it, and the union of those calls'
    # pieces. Both are built at most once per batch: a result is weighed
    # against several calls only when the batch is already in doubt, and doubt
    # keeps every name in `cand`, so from the first uncertain result the set
    # does not move. Rebuilding them per result is O(distinct names), which is
    # half of what made a wide turn of differently named tools quadratic.
    cand_view: Optional[frozenset] = None
    cand_pieces: Optional[frozenset] = None
    # The names still open, the names a result could have answered
    # (`open_names | doubt`), how many calls of the batch those names cover,
    # and how many of them the operator declared as handing the model's text
    # back. All four are kept as the batch changes rather than rebuilt for
    # every result: rebuilding them was O(distinct names) per result, which
    # made a turn of N calls with N different names quadratic - 4000 calls
    # took 4.0 s against 0.04 s for the same calls under one name.
    open_names: Set[str] = set()
    cand: Set[str] = set()
    cand_total = 0
    cand_echo = 0
    doubt_covers_cand = False
    reused_ids: Set[str] = set()
    batch_ids: Set[str] = set()
    # Ids this turn declared more than once. Only these are ambiguous inside the
    # turn; an id reused from an EARLIER turn is not (see the result branch).
    batch_dup_ids: Set[str] = set()
    guessed: List[str] = []
    unmatched: List[str] = []
    unresolved: List[str] = []
    echoed_back: List[str] = []   # results that quoted their own call back
    # Results whose last line the model wrote into a call of an EARLIER turn:
    # kept as evidence and reported - see `_hands_back_what_it_was_given`.
    earlier_echoes: List[str] = []
    # The same notes as data, with the tool name kept apart: a name parsed back
    # out of the text above could be forged by a tool's name.
    earlier_echo_details: List[Dict[str, str]] = []
    # Every call of the turn now open, answered or not: how many per name, and
    # the pieces of their arguments, all together and per name.
    batch_count: Dict[str, int] = {}
    # The calls of this turn, and of every turn before it, as indexed word
    # sequences. A result is weighed against them by the same measure and the
    # same number as against the call it answered: the seventh round left this
    # path on the old "more than half of the reply, as a whole unit" reading,
    # and the sixth round's three characters walked through it unchanged.
    batch_text = _Words()
    # And as sets of whole values, for the one question coverage cannot ask: a
    # reply too short to hold a run of ECHO_MIN_CHARS. An interpreter that kept
    # `answer = "B"` from an earlier turn and prints `B` hands back the model's
    # own value, and no share of anything can see it.
    batch_values: Set[str] = set()
    earlier_values: Set[str] = set()
    batch_pieces_by_name: Dict[str, Set[str]] = {}
    # The pieces of every call from turns already closed. Each call is read
    # once, when it is declared, and each result is a set lookup - so reading a
    # long run stays linear, not quadratic.
    earlier_text = _Words()
    # Every message the model wrote - not its calls - as whole values, in the
    # forms a value is compared in. A memory or history tool that hands back
    # what the assistant SAID a turn ago was weighed against calls only and
    # passed with no note: a tool_result root, BOOKS BALANCE, not a word
    # (review 20, 2.1). Whole messages only, with no share in it. Two readings
    # by share were tried first and measured on AgentHallu: against every
    # message, 19 notes and no echo among them (a tool returning a file the
    # model had quoted, a search repeating the question); with runs the model
    # had copied from outside set aside, 4 notes and still none (a search
    # repeating the model's plan). Text does not say which way a shared
    # sentence went, so this asks only whether a piece holding most of the
    # reply - the reply, a line, a `label: ` value, a JSON value
    # (`_echo_candidates`) - IS a whole message the model wrote.
    message_values: Set[str] = set()
    # Their lengths in letters and digits, sorted. A piece `_echo_candidates`
    # returns holds more than half of the reply's letters, so it can only equal
    # a message whose length is in (half the reply, the reply]; a reply with no
    # such message is not taken apart at all. Without this the reading of
    # AgentHallu took 14% longer; with it, the question costs a count.
    message_lengths: List[int] = []
    # Artifacts demoted against more than one call the log could not tell apart.
    # They are the model's text, but not known to be the one the user saw.
    unconfident_ids: Set[str] = set()
    step_ids: Set[str] = set()
    # The exact re-weighing MAX_STARTS may call for, bounded over the whole log.
    budget = _Budget()
    # Warnings the reading worked out and did not raise, because the operator
    # declared the tool external - each with the declaration that cleared it.
    cleared: List[Dict[str, Any]] = []

    def step_id(base: str) -> str:
        """Two tool batches under one turn must not share an id: the report
        names steps, and a name that points at two things names neither."""
        sid, n = base, 1
        while sid in step_ids:
            n += 1
            sid = f"{base}_{n}"
        step_ids.add(sid)
        return sid

    def _close(name: str) -> None:
        """One call of `name` has been answered. Where it was the last one open,
        the name leaves the candidate set unless doubt has already claimed it."""
        nonlocal cand_total, cand_echo, cand_view, cand_pieces
        if open_count.get(name):
            return
        open_names.discard(name)
        if name in cand and name not in doubt:
            cand.discard(name)
            cand_view = cand_pieces = None
            cand_total -= batch_count.get(name, 0)
            if _key(name) in echo and name != PLACEHOLDER:
                cand_echo -= 1

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
                # As written only, cleaned, trailing marks trimmed one at a time:
                # a reply's escapes are undone on the reply's side
                # (`_echo_candidates`), and decoding every long message of a
                # CodeAct run here cost more than the rest of the reading.
                said = _norm(_clean(content))
                while _alnum(said) >= ECHO_MIN_CHARS:
                    if said not in message_values:
                        message_values.add(said)
                        bisect.insort(message_lengths, _alnum(said))
                    if said[-1] not in _TRAILING:
                        break
                    said = said[:-1]
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
                open_names = set()
                cand = set()
                cand_total = 0
                cand_echo = 0
                doubt_covers_cand = False
                cand_view = cand_pieces = None
                batch_ids = set()
                batch_dup_ids = set()
                # The turn before this one is closed: its arguments become what
                # a later result is checked against for an echo from earlier.
                earlier_text.absorb(batch_text)
                batch_text = _Words()
                earlier_values |= batch_values
                batch_values = set()
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
                if given:
                    named_in_log.add(_key(given))
                open_count[named] = open_count.get(named, 0) + 1
                batch_count[named] = batch_count.get(named, 0) + 1
                if named in cand:
                    cand_total += 1
                if named not in open_names:
                    open_names.add(named)
                    if named not in cand:
                        cand.add(named)
                        cand_view = cand_pieces = None
                        cand_total += batch_count[named]
                        if _key(named) in echo and named != PLACEHOLDER:
                            cand_echo += 1
                        doubt_covers_cand = False
                batch_text.add_call(args_sent)
                batch_values |= _call_values(args_sent)
                batch_pieces_by_name.setdefault(named, set()).update(entry.pieces)
                if cid:
                    open_with_id += 1
                    by_id.setdefault(cid, entry)
                    calls_by_id[cid] = entry
                    if tool_names.get(cid, named) != named or cid in batch_ids:
                        reused_ids.add(cid)
                    if cid in batch_ids:
                        batch_dup_ids.add(cid)
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
            # several tools: a declaration by name must not clear that note.
            name_known = bool(named_itself)
            # Did the LOG give this name, or is `tool` the reader's placeholder?
            # A declaration may only reach a name the log actually carries.
            named_by_log = bool(named_itself)
            # The call this result consumes, applied only once every question
            # that depends on what is still open has been answered. The old
            # reading took a snapshot of the open names at this point and read
            # it afterwards; closing the call any earlier answers those
            # questions against a queue this result has already emptied.
            consumed: Optional[Tuple[str, bool]] = None
            tool, matched = named_itself, False

            if cid and ((cid in by_id and cid not in batch_dup_ids)
                        or (cid in tool_names and cid not in reused_ids)):
                # An id we have seen names its call outright, whichever turn
                # declared it - unless the same id was declared for two
                # different tools, in which case it names nothing and must not
                # be allowed to claim it does.  If that call is still open here
                # it is answered; if it is not, this result belongs to an
                # earlier turn and must take no slot from this one.
                #
                # Except where THIS turn declared the id, once. Agents that
                # number calls turn by turn write `call_0` in every turn; the
                # id then names two tools across the log, and the rule above
                # sent `final_answer(X)` answering X to "matched nothing open",
                # a tool_result root with the demotion gone (review 20, 2.1).
                # Inside its own turn such an id is open and unambiguous, and a
                # result answers the turn it follows.
                tool = tool or tool_names[cid]
                own = calls_by_id.get(cid)
                sent = own.args if own is not None else ""
                name_known = name_known or (own is not None and own.named)
                named_by_log = named_by_log or (own is not None and own.named)
                entry = by_id.get(cid)
                if entry is not None and not entry.done:
                    matched = True
                    # The result's own name wins over the call's: a gateway
                    # that renames a tool between call and result is saying
                    # what answered, and that is the more direct evidence.
                    tool = named_itself or entry.name
                    named_by_log = bool(named_itself) or entry.named
                    entry.done = True
                    consumed = (entry.name, True)
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
                    matched = True
                    named_by_log = True
                    if open_count[named_itself] == 1:
                        own, sent = entry, entry.args
                    else:
                        confident, could_be = False, {named_itself}
                    entry.done = True
                    consumed = (named_itself, bool(entry.cid))
                if not matched:
                    unmatched.append(f"tool[{k}] ({named_itself})")
                    unplaced = True
            elif open_names:
                while open_calls[head].done:
                    head += 1
                entry = open_calls[head]
                tool = entry.name
                entry.done = True
                consumed = (entry.name, bool(entry.cid))
                # Doubt is contagious within a batch. Once one result has been
                # placed by position alone, the calls left in the queue are not
                # known to be the ones still unanswered - so a later result
                # that looks unambiguous ("only `search` is left") is not. That
                # is how the very first interleaving this rule was written for
                # still recorded an echo as evidence: the wrong guess made the
                # next guess look certain.
                matched = len(cand) == 1
                # Placed by position, the result may answer any call of this
                # turn still in question - so it is weighed against all of them.
                if cand_total == 1:
                    own, sent = entry, entry.args
                else:
                    if cand_view is None:
                        cand_view = frozenset(cand)
                        cand_pieces = frozenset().union(
                            *(batch_pieces_by_name.get(n, frozenset()) for n in cand_view))
                    confident, could_be = False, cand_view
                name_known = matched and entry.named
                named_by_log = entry.named
                if not matched and not doubt_covers_cand:
                    # Doubt spreads over every name still in question, once per
                    # batch: after this every one of them is already in `doubt`,
                    # and no call is declared after the results start arriving.
                    doubt |= cand
                    doubt_covers_cand = True
                guessed.append(f"tool[{k}] -> {tool}"
                               + ("" if matched else " (by position only)"))
            else:
                guessed.append(f"tool[{k}] -> unknown")
                unplaced = True

            tool = tool or PLACEHOLDER
            # A declaration reaches this result only where the LOG gave the name.
            # Where it did not, `tool` is this reader's own placeholder, and
            # declaring the reader's word must not turn every unnamed result in
            # the file into external text - nor silence a warning about one.
            dkey = _key(tool) if named_by_log else ""
            # Uncertain, and one of the calls it might have answered echoes the
            # model: the file does not say which, so it is not evidence. Where
            # nothing is open and nothing is in doubt, it could have been any
            # tool in the run - which is only a question at all if some of them
            # echo. Counted, not collected: naming them is only needed for the
            # message, and collecting them for every result is what a wide turn
            # cannot afford.
            # A result's own name settles what it is - unless this batch
            # declared calls and none of them bears that name, in which case
            # the log contradicts itself and the name settles nothing.
            name_trusted = bool(named_itself) and (matched or not open_names)
            ambiguous = (not matched and not name_trusted
                         and (cand_echo > 0 if cand else bool(echo)))
            if consumed is not None:
                closed_name, closed_had_id = consumed
                open_count[closed_name] -= 1
                _close(closed_name)
                if closed_had_id:
                    open_with_id -= 1
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
                # The union is cached only for the whole candidate set, which
                # is the wide case; a result placed by its own name is weighed
                # against that one name's calls.
                pieces = (cand_pieces if could_be is cand_view else
                          frozenset().union(*(batch_pieces_by_name.get(n, frozenset())
                                              for n in could_be)))
                handed_back = _stands_among(content, [pieces or frozenset()])
            # Also asked before the cut, for the same reason. Only for a result
            # that stays evidence: a warning is a report, never a demotion.
            warning: Optional[Tuple[str, str]] = None      # (kind, the line)
            # A tool declared external, known by its own name, is vouched for:
            # its warnings are not raised. They are still worked out, and kept
            # with the declaration that cleared them - a warning taken away in
            # silence is a pass nobody can see. `handed_back` above was decided
            # before this and is not touched by the declaration.
            vouched = name_known and dkey in external
            if not handed_back and dkey not in echo and not ambiguous:
                if unplaced:
                    warning = ("unmatched", _matched_line(content))
                else:
                    # Three questions, one measure, one number. The call this
                    # result answered first, because it is the one the log
                    # actually ties to it; then the turns before this one; then
                    # the rest of this turn. The answering call's own text is
                    # in the batch index too, so a same-turn report can only be
                    # reached when that call alone did not carry the reply -
                    # which is what "another call of this turn" means.
                    partly, unsure = _partly_hands_back(content, sent, budget)
                    if partly:
                        warning = ("answering_call", partly)
                    else:
                        shown = _short(_norm(content))
                        # The whole reply as a value: as written, and with the
                        # quotes around it taken off.
                        forms: List[str] = []
                        for text in dict.fromkeys((content, _unquoted(content))):
                            whole = _norm(_clean(text))
                            forms += [f for f in _forms(text)
                                      if len(f) > 1 or f == whole]
                        share, run, unsure_earlier = _share_in(content, earlier_text, budget)
                        if share >= WARN_SHARE:
                            warning = ("earlier_turn", run)
                        elif any(f in earlier_values for f in forms):
                            warning = ("earlier_turn", shown)
                        else:
                            share, run, unsure_batch = _share_in(content, batch_text, budget)
                            if share >= WARN_SHARE:
                                warning = ("same_turn", run)
                            elif any(f in batch_values for f in forms):
                                warning = ("same_turn", shown)
                            else:
                                said = ""
                                letters = _alnum(content) if message_values else 0
                                if letters and (
                                        ("\\" in content and len(content) <= UNESCAPE_RESULT_CHARS)
                                        or bisect.bisect_right(message_lengths, letters)
                                        > bisect.bisect_right(message_lengths, letters // 2)):
                                    said = next((c for c in _echo_candidates(content)
                                                 if c in message_values), "")
                                if said:
                                    warning = ("model_message", said)
                                elif unsure or unsure_earlier or unsure_batch:
                                    # Not "no echo here": "not weighed to the end".
                                    warning = ("unchecked", shown)
            if len(content) > max_tool_chars:
                content = content[:max_tool_chars]
                cut = True
            else:
                cut = False
            aid = f"t{k}"
            if dkey in echo or ambiguous or handed_back:
                kind = "intermediate"       # the model's own words handed back
                if handed_back and dkey not in echo:
                    echoed_back.append(f"tool[{k}] ({tool}): "
                                       + _short(_matched_line(content)))
                if ambiguous:
                    unresolved.append(
                        f"tool[{k}] could have answered any of "
                        + ", ".join(sorted((set(cand) or {echo[key] for key in echo})
                                           | {tool}))
                        + "; read as the model's own text because one of them "
                          "hands the model's words back")
            elif dkey in verbatim:
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
                detail = {"result": f"tool[{k}]", "tool": tool if name_known else "",
                          "line": _short(line), "message": file_index, "call_id": call_id,
                          "kind": kind_of}
                if vouched:
                    cleared.append({**detail, "text": f"{where}, {_WARNING_KINDS[kind_of]}: "
                                                      f"{_short(line)}",
                                    "cleared_by": f"--tool-returns-external {external[dkey]}"})
                else:
                    earlier_echoes.append(f"{where}, {_WARNING_KINDS[kind_of]}: {_short(line)}")
                    earlier_echo_details.append(detail)
            artifacts.append({"artifact_id": aid, "kind": kind,
                              "title": tool, "content": content})
            if cut:
                truncated.append(aid)
            if ambiguous:
                unsure_ids.add(aid)
            if handed_back and not confident and dkey not in echo:
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
        "declarations_not_in_log": sorted(
            spelling for table in (echo, verbatim, external)
            for key, spelling in table.items() if key not in named_in_log),
        "echoed_back_tool_results": echoed_back,
        "echoes_from_earlier_turns": earlier_echoes,
        "echo_warning_details": earlier_echo_details,
        "echo_warnings_cleared_by_declaration": cleared,
        "notes": notes,
    }
    return {"artifacts": artifacts, "steps": steps, "_meta": meta}
