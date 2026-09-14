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

**Anything short of that is a warning, not a demotion.** A value of the call
standing somewhere inside a longer reply, or enough of the reply covered by the
call's own text (`WARN_SHARE`), is recorded in `_meta.echo_warning_details` with
kind `answering_call`. It is not demoted, because a tool reporting a real fact
about the argument it was handed looks the same - `rm: cannot remove 'X': No
such file or directory`, a search result quoting its query in a header, an
order confirmation repeating the price it was given. Measured on AgentHallu:
letting every such match demote costs 448 demotions beyond the whole reply,
most of them real tool output. So the reading says what it found and stops: the
run does not pass until a person confirms the tool by name.

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
    "answering_call": "text from the call it answered, short of the whole reply",
}


#: How much of a reply must stand in the answering call's own text before the
#: reading says so out loud. The ONE number in this file's answering-call path,
#: and it moves only WARNINGS - a demotion never consults it, so no setting of
#: it can turn the model's own words into evidence. Chosen on AgentHallu as the
#: lowest of the three measured (30/20/10%): at every one of them the scheme
#: loses nothing the old rule caught, so the lowest was taken, which is the one
#: an attacker must dilute hardest to get under.
WARN_SHARE = 0.10


def _forms(text: str) -> List[str]:
    """`text` as a value might be written: each of its spellings, whitespace
    collapsed, and its trailing punctuation trimmed ONE MARK AT A TIME.

    One mark at a time because the run used to be stripped whole, which put the
    form with no punctuation and the form with two into the set but never the
    one with one - and one is what the arguments carry when a sentence that
    already ended in a period comes back with another added."""
    out: List[str] = []
    for spelling in _spellings(text):
        value = _norm(spelling)
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
    value handed back."""
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
    whole = _norm(result)
    for form in _reply_values(result, whole_only):
        if form in values and (len(form) > 1 or form == whole):
            return form
    return ""


@functools.lru_cache(maxsize=512)
def _arg_words(args: str) -> Tuple[Tuple[str, ...], Dict[str, Tuple[int, ...]]]:
    """The call's arguments as a word sequence with an index of where each word
    stands, in each spelling that matters: as written, its quoted literals, and
    its JSON values. The spellings are separated by a word no reply can hold,
    so a run can never be matched across the join."""
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
    words: List[str] = []
    for text in texts:
        words.extend(text.split())
        words.append("\x00")
    where: Dict[str, List[int]] = {}
    for i, word in enumerate(words):
        where.setdefault(word, []).append(i)
    return tuple(words), {k: tuple(v) for k, v in where.items()}


def _echo_share(result: str, sent: str) -> Tuple[float, str]:
    """How much of the reply, in letters and digits, is text the call carried.

    Both sides are read as words, so every run of whitespace is one space and a
    newline dropped into the middle of an echo changes nothing - which is how
    three characters used to empty this rule. The reply is tiled left to right
    by maximal runs that stand in the arguments; a run shorter than
    ECHO_MIN_CHARS is not counted, because two texts in the same language share
    short strings. Greedy, so it can only UNDER-count."""
    total = _alnum(result)
    if not total or not sent:
        return 0.0, ""
    words, where = _arg_words(sent)
    reply = result.split()
    covered = 0
    best_run, best = 0, ""
    i = 0
    while i < len(reply):
        live = where.get(reply[i])
        if not live:
            i += 1
            continue
        run = 1
        while True:
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
                best_run, best = matched, " ".join(reply[i:i + run])
        i += run
    return covered / total, best


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

    What it costs, counted afresh on AgentHallu for proverka7 (the corpus as
    bench/openai_roundtrip.py renders it; tests/test_openai_roundtrip_numbers.py
    pins the figures and bench/echo_coverage.py measures the threshold). With
    the corpus's four echo tools declared it fires 313 times over 3535 tool
    results, 177 of them on tools nobody declared. Against the reader written
    for that corpus by hand it is stricter on 157 artifacts and laxer on none -
    on this corpus. That last clause is not decoration: "laxer on none" is a
    measurement of these 693 trajectories, not a property of the code, and the
    sixth review's three-character bypass was exactly a laxer case that this
    corpus does not contain.

    What the warning path costs: 989 warnings, and 155 of 693 trajectories
    (22.4%) then need a person to confirm a tool by name before the run can
    pass. That is the price of not guessing, and it is roughly three times what
    a threshold-only reading of the same corpus suggested, because an exact
    value match anywhere in a reply warns however small it is.

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


def _partly_hands_back(result: str, sent: str) -> str:
    """Is there ANY match with the answering call short of the whole reply?

    Either a value the call carried stands somewhere inside the reply - as a
    line, a JSON value, the text after a label, a quoted literal - or enough of
    the reply is covered by the call's own text (`WARN_SHARE`).

    This is a warning, never a demotion. The reading cannot tell a value handed
    back from a fact a tool reported about the value it was given, and it is
    not allowed to guess: it says so, and the run does not pass until a person
    confirms the tool by name. Returns the text it matched, or "" - the warning
    quotes it, so the person reading the report sees what the reading saw."""
    if not sent:
        return ""
    found = _is_a_value_of(result, sent, whole_only=False)
    if found:
        return found
    share, run = _echo_share(result, sent)
    return run if share >= WARN_SHARE else ""


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
#: neither declared nor confirmed - the same discipline the echo gate already
#: applies to a warning whose tool is unknown.
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
            # Did the LOG give this name, or is `tool` the reader's placeholder?
            # A declaration may only reach a name the log actually carries.
            named_by_log = bool(named_itself)
            # The call this result consumes, applied only once every question
            # that depends on what is still open has been answered. The old
            # reading took a snapshot of the open names at this point and read
            # it afterwards; closing the call any earlier answers those
            # questions against a queue this result has already emptied.
            consumed: Optional[Tuple[str, bool]] = None
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
                named_by_log = named_by_log or (own is not None and own.named)
                entry = by_id.get(cid)
                if entry is not None and not entry.done:
                    expected, matched = entry.name, True
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
                    expected, matched = entry.name, True
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
                expected = tool = entry.name
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
            # A tool declared external, known by its own name, gets no warning:
            # the operator vouched for it. `handed_back` above was decided
            # before this and is not touched by the declaration.
            vouched = name_known and dkey in external
            if not handed_back and dkey not in echo and not ambiguous and not vouched:
                if unplaced:
                    warning = ("unmatched", _matched_line(content))
                else:
                    mine = own.pieces if own is not None else frozenset()
                    for piece in _echo_candidates(content):
                        if piece in mine:
                            continue        # its own call; weighed in full below
                        if not could_be and piece in batch_pieces:
                            warning = ("same_turn", piece)
                            break
                        if piece in earlier_pieces:
                            warning = ("earlier_turn", piece)
                            break
                    # The answering call, in full. The demotion above asks only
                    # whether the whole reply IS a value it was given; anything
                    # short of that - a value standing inside the reply, or
                    # enough of the reply covered by the call's own text - the
                    # reading cannot settle, so it says so rather than guessing.
                    if warning is None:
                        partly = _partly_hands_back(content, sent)
                        if partly:
                            warning = ("answering_call", partly)
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
        "notes": notes,
    }
    return {"artifacts": artifacts, "steps": steps, "_meta": meta}
