"""Read an OpenTelemetry GenAI span export as a raw tallystick trace.

    tallystick check-trace --from otel spans.json
    tallystick convert     --from otel spans.json -o trace.json

What this reads
---------------
An OTLP/JSON export (`{"resourceSpans": [...]}`), a bare list of spans, or
`{"spans": [...]}` - whichever your collector, file exporter or SDK wrote.
Attributes are accepted both in OTLP's `[{"key", "value": {"stringValue"}}]`
form and as a plain `{"key": "value"}` object.

Spans become a conversation, and the conversation becomes a trace through the
OpenAI reader, so both paths produce the same shape and one set of rules about
roots applies to both.

Three ways a log carries the text, read in this order
-----------------------------------------------------
1. `gen_ai.input.messages` / `gen_ai.output.messages` - the current GenAI
   semantic convention. Parts carry `type` in `text`, `tool_call`,
   `tool_call_response`.
2. `gen_ai.prompt.{n}.role` / `.content` and `gen_ai.completion.{n}.*` - the
   indexed attributes OpenLLMetry and Traceloop have written for years.
3. Span events named `gen_ai.system.message`, `gen_ai.user.message`,
   `gen_ai.assistant.message`, `gen_ai.tool.message`, `gen_ai.choice` - the
   earlier convention, still emitted by several SDKs.

Which one a file was read by is recorded in `_meta.otel.reading`. They are not
equivalent: only the first distinguishes a tool call from its response, so a
log read by 2 or 3 may place a tool result one turn from where it happened.

The honest warning
------------------
**Content on a GenAI span is Opt-In.** Most deployments do not record it,
because prompts and completions are the expensive, sensitive part of a trace.
A span export without content produces a trace with nothing to audit, and this
reader says so (`no content recorded`) rather than returning an empty run that
`check-trace` would then report on as though it were the agent's fault.

**This reader has been tested against the specification and against logs built
to match it, not against a corpus of real exports** - unlike the OpenAI reader,
which was checked on 693 recorded agent trajectories. The GenAI conventions are
Development status and still move. Treat a surprising result here as a bug in
the reader until you have looked at the span yourself.

Time order
----------
Spans are sorted by `startTimeUnixNano`, so an export that arrived out of
order still reads as the run that happened. A span with no time keeps its place
behind the last span that had one, so one untimed span cannot make the whole
conversation read backwards.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .openai_chat import DEFAULT_MAX_TOOL_CHARS
from .openai_chat import to_trace as _messages_to_trace

_ROLE_EVENTS = {
    "gen_ai.system.message": "system",
    "gen_ai.user.message": "user",
    "gen_ai.assistant.message": "assistant",
    "gen_ai.tool.message": "tool",
}


# --------------------------------------------------------------------- values

def _scalar(value: Any) -> Any:
    """Unwrap an OTLP AnyValue (`{"stringValue": "x"}`) to a plain value."""
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue"):
        if key in value:
            return value[key]
    for key in ("intValue", "doubleValue"):
        if key in value:
            return value[key]
    if "arrayValue" in value:
        inner = value["arrayValue"]
        values = inner.get("values", []) if isinstance(inner, dict) else []
        return [_scalar(v) for v in values]
    if "kvlistValue" in value:
        inner = value["kvlistValue"]
        pairs = inner.get("values", []) if isinstance(inner, dict) else []
        return {str(p.get("key")): _scalar(p.get("value"))
                for p in pairs if isinstance(p, dict)}
    if "bytesValue" in value:
        return value["bytesValue"]
    return value


def _attrs(raw: Any) -> Dict[str, Any]:
    """Accept OTLP's key/value list or a plain attribute object."""
    if isinstance(raw, dict):
        return {str(k): _scalar(v) for k, v in raw.items()}
    if isinstance(raw, list):
        out: Dict[str, Any] = {}
        for item in raw:
            if isinstance(item, dict) and "key" in item:
                out[str(item["key"])] = _scalar(item.get("value"))
        return out
    return {}


class Unreadable(list):
    """An attribute that held content this reader could not parse - almost
    always a message list cut by OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT, which
    leaves invalid JSON. It is an empty list so every caller keeps working, and
    a distinguishable one so the loss is reported instead of being blamed on
    the recorder as "the step declares no inputs"."""


def _as_list(value: Any) -> List[Any]:
    """A message list may arrive parsed, or as the JSON string the convention
    actually puts on the wire."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except ValueError:
            return Unreadable()
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return value
    return []


# ---------------------------------------------------------------------- spans

def _spans_of(data: Any) -> List[Dict[str, Any]]:
    """Pull the flat span list out of whatever wrapper the exporter used."""
    spans: List[Dict[str, Any]] = []
    if isinstance(data, list):
        spans = [s for s in data if isinstance(s, dict)]
    elif isinstance(data, dict):
        if isinstance(data.get("resourceSpans"), list):
            for rs in data["resourceSpans"]:
                if not isinstance(rs, dict):
                    continue
                for ss in rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []:
                    if isinstance(ss, dict):
                        spans.extend(s for s in (ss.get("spans") or [])
                                     if isinstance(s, dict))
        elif isinstance(data.get("spans"), list):
            spans = [s for s in data["spans"] if isinstance(s, dict)]
        elif "name" in data and ("attributes" in data or "events" in data):
            spans = [data]                      # a single span, as some SDKs write
    if not spans:
        raise ValueError(
            "no spans found: an OTel export is {'resourceSpans': [...]}, "
            "{'spans': [...]}, or a bare list of spans")

    def start(span: Dict[str, Any]) -> Optional[int]:
        for key in ("startTimeUnixNano", "start_time_unix_nano", "startTime"):
            value = span.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
        return None

    # A span with no time keeps its place behind the last span that had one,
    # rather than disabling the sort for the whole file: one untimed span in an
    # export should not make the conversation read backwards.
    keyed: List[Tuple[int, int, Dict[str, Any]]] = []
    carried = 0
    for i, span in enumerate(spans):
        when = start(span)
        if when is not None:
            carried = when
        keyed.append((carried, i, span))
    return [s for _t, _i, s in sorted(keyed, key=lambda x: (x[0], x[1]))]


def looks_like_otel(data: Any) -> bool:
    """Cheap shape test for `--from auto` and for the hint on a failed load."""
    try:
        spans = _spans_of(data)
    except ValueError:
        return False
    for span in spans:
        attrs = _attrs(span.get("attributes"))
        if any(k.startswith("gen_ai.") for k in attrs):
            return True
        for event in span.get("events") or []:
            if isinstance(event, dict) and str(event.get("name", "")).startswith("gen_ai."):
                return True
    return False


# ----------------------------------------------------------------- conversion

def _parts_to_message(role: str, parts: Any) -> List[Dict[str, Any]]:
    """One semconv message -> the OpenAI messages it stands for.

    A single assistant message may carry both text and tool calls, and a `user`
    message may carry a `tool_call_response` part, which is not a user turn at
    all - it is the tool speaking. Splitting here keeps the OpenAI reader's
    rules about roots intact."""
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    responses: List[Dict[str, Any]] = []
    for part in _as_list(parts):
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        ptype = str(part.get("type", "text"))
        if ptype == "tool_call":
            tool_calls.append({
                "id": str(part.get("id") or ""),
                "type": "function",
                "function": {"name": str(part.get("name") or "tool"),
                             "arguments": part.get("arguments")},
            })
        elif ptype == "tool_call_response":
            body = part.get("response", part.get("result"))
            responses.append({
                "role": "tool",
                "tool_call_id": str(part.get("id") or ""),
                "name": str(part.get("name") or ""),
                "content": body if isinstance(body, str)
                else json.dumps(body, ensure_ascii=False) if body is not None else "",
            })
        else:
            for key in ("content", "text"):
                value = part.get(key)
                if isinstance(value, str):
                    text_parts.append(value)
                    break
                if value is not None:
                    text_parts.append(json.dumps(value, ensure_ascii=False))
                    break

    out: List[Dict[str, Any]] = []
    body = "\n".join(t for t in text_parts if t)
    if body or tool_calls:
        message: Dict[str, Any] = {"role": role or "assistant", "content": body}
        if tool_calls:
            message["tool_calls"] = tool_calls
        out.append(message)
    out.extend(responses)
    return out


def _semconv_messages(attrs: Dict[str, Any],
                      key: str) -> Tuple[List[Dict[str, Any]], int]:
    """The current convention. `role` is expected on every message, but an
    exporter that omits it must not have its turns read as roots: a turn
    wrongly called the model's own is a false alarm, a turn wrongly called
    evidence is laundering. So the default is `assistant` on both sides, and
    the count of turns that needed it is returned to be reported."""
    out: List[Dict[str, Any]] = []
    roleless = 0
    for entry in _as_list(attrs.get(key)):
        if isinstance(entry, dict):
            role = str(entry.get("role") or "")
            if not role:
                role = "assistant"
                roleless += 1
            out.extend(_parts_to_message(role, entry.get("parts", entry.get("content"))))
        elif isinstance(entry, str):
            roleless += 1
            out.append({"role": "assistant", "content": entry})
    return out, roleless


def _indexed_messages(attrs: Dict[str, Any], prefix: str,
                      default_role: str) -> Tuple[List[Dict[str, Any]], int]:
    """`gen_ai.prompt.0.role` / `gen_ai.prompt.0.content`, the Traceloop shape.

    `default_role` is used only where the log gives no role. That case is not
    rare - OTel drops attributes past its per-span limit, and some exporters
    record content alone - and the choice of default decides whether an
    unattributed turn is read as evidence or as the model's own words. It is
    `assistant` for the prompt side: a turn wrongly called the model's produces
    a false alarm, while a turn wrongly called a root is laundering, and this
    reader errs towards the alarm. The count of such turns is returned so the
    reading can be reported."""
    indices = set()
    for key in attrs:
        if key.startswith(prefix):
            head = key[len(prefix):].split(".", 1)[0]
            if head.isdigit():
                indices.add(int(head))
    out: List[Dict[str, Any]] = []
    roleless = 0
    for n in sorted(indices):
        base = f"{prefix}{n}."
        role = str(attrs.get(base + "role") or "")
        if not role:
            role = default_role
            roleless += 1
        content = attrs.get(base + "content")
        if content is None:
            content = attrs.get(base + "message.content")
        text = content if isinstance(content, str) else (
            "" if content is None else json.dumps(content, ensure_ascii=False))
        name = attrs.get(base + "tool_calls.0.name") or attrs.get(base + "name")
        message: Dict[str, Any] = {"role": role, "content": text}
        if role == "tool" and name:
            message["name"] = str(name)
        if text.strip() or role == "tool":
            out.append(message)
    return out, roleless


def _event_messages(span: Dict[str, Any], dropped: Optional[List[str]] = None,
                    from_choice: Optional[List[int]] = None
                    ) -> List[Dict[str, Any]]:
    """`from_choice`, if given, is appended to once for each message that came
    from a `gen_ai.choice` - the model speaking now. A
    `gen_ai.assistant.message` is replayed history and must not count, or the
    reader stops warning that an export recorded no output at all."""
    out: List[Dict[str, Any]] = []
    dropped = dropped if dropped is not None else []
    from_choice = from_choice if from_choice is not None else []
    for event in span.get("events") or []:
        if not isinstance(event, dict):
            continue
        ename = str(event.get("name", ""))
        attrs = _attrs(event.get("attributes"))
        body = attrs.get("content", attrs.get("gen_ai.event.content"))
        if ename in _ROLE_EVENTS:
            role = _ROLE_EVENTS[ename]
            text = body if isinstance(body, str) else (
                "" if body is None else json.dumps(body, ensure_ascii=False))
            message: Dict[str, Any] = {"role": role, "content": text}
            if role == "tool":
                message["name"] = str(attrs.get("gen_ai.tool.name") or "tool")
                message["tool_call_id"] = str(attrs.get("id") or "")
            out.append(message)
        elif ename == "gen_ai.choice":
            payload = body
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    # Not JSON: the event carried the answer as plain text,
                    # which several SDKs write. It is still the model speaking -
                    # unless it said nothing, in which case counting it as
                    # output would suppress the warning that there is none.
                    out.append({"role": "assistant", "content": payload})
                    if payload.strip():
                        from_choice.append(1)
                    continue
            # A choice may be the message, or the whole `choices` array the API
            # returned. Both are written in the wild; dropping the second would
            # lose the answer itself and leave the run with nothing to audit.
            if isinstance(payload, list):
                first = next((p for p in payload if isinstance(p, dict)), None)
                if first is None:
                    dropped.append(f"{ename} held a list with no message in it")
                    continue
                payload = first
            if isinstance(payload, dict):
                inner = payload.get("message", payload)
                if not isinstance(inner, dict):
                    inner = payload
                text = inner.get("content")
                calls = inner.get("tool_calls")
                if not calls and (text is None or (not isinstance(text, str)
                                                    and not text)):
                    # None, "", [], {}, 0, False - every spelling of "the model
                    # said nothing". Rendering one as `"[]"` would make it the
                    # run's answer and suppress the warning that there is none.
                    dropped.append(f"{ename} carried no content")
                    continue
                message = {"role": "assistant",
                           "content": text if isinstance(text, str) else
                           ("" if text is None else json.dumps(text, ensure_ascii=False))}
                if isinstance(calls, list) and calls:
                    message["tool_calls"] = calls
                out.append(message)
                # `{"content": ""}` is what the conventions and the OpenAI
                # SDKs write for a choice that said nothing. Counting it as
                # output suppresses the warning that there is none - the same
                # bug as the plain-text case above, in the commoner spelling.
                if message["content"].strip() or message.get("tool_calls"):
                    from_choice.append(1)
            elif payload is not None:
                dropped.append(f"{ename} was a {type(payload).__name__}, not a choice")
    return out


def _merge(emitted: List[Dict[str, Any]], incoming: List[Dict[str, Any]]
           ) -> Tuple[List[Dict[str, Any]], int, bool]:
    """Every chat span carries the whole conversation so far, so the same turns
    appear on span after span. Keep only what is new, by matching the longest
    prefix already emitted. A prefix that breaks early means the history was
    rewritten between calls (a compacted or re-ordered prompt); that is
    reported rather than hidden, because the run then is not a single
    conversation and a claim's inputs are not what they appear to be."""
    def sig(m: Dict[str, Any]) -> Tuple[str, str]:
        return (str(m.get("role", "")), str(m.get("content", "")))

    n = 0
    while n < len(emitted) and n < len(incoming) and sig(emitted[n]) == sig(incoming[n]):
        n += 1
    if n == len(emitted):
        return incoming[n:], 0, False

    # The prefix broke. The usual cause is a sliding window: the prompt was
    # trimmed from the front, so this span's history is a SUFFIX of what we
    # have rather than an extension of it. Align on that first - it recovers
    # the run exactly, and it does not touch turns that genuinely repeat.
    aligned: List[int] = []
    for start in range(1, len(emitted) + 1):
        tail = emitted[start:]
        if tail and len(tail) <= len(incoming) and \
                all(sig(a) == sig(b) for a, b in zip(tail, incoming)):
            aligned.append(len(tail))
    if aligned:
        # More than one alignment fits when the conversation repeats itself -
        # a driver that re-prompts with "continue" every turn. The longest is
        # taken, which is the reading that adds nothing twice; if a turn
        # genuinely repeated, that reading has lost it, so the ambiguity is
        # returned as a dropped turn rather than passed off as certainty.
        # What is at stake is not the number of fittings but how many turns
        # the shortest of them would have added that the longest does not.
        return incoming[aligned[0]:], aligned[0] - aligned[-1], True

    # No alignment: this span's prompt is not this conversation's history.
    # Turns already recorded are not recorded again - two artifacts with the
    # same text is the one thing a provenance audit cannot cope with - but the
    # number dropped is returned, because a turn that genuinely repeated is
    # lost here and silence about that would be its own kind of false record.
    known = {sig(m) for m in emitted}
    kept = [m for m in incoming[n:] if sig(m) not in known]
    return kept, len(incoming[n:]) - len(kept), True


def to_messages(data: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Rebuild the conversation from a span export. Returns the messages and
    what had to be said about the reading."""
    spans = _spans_of(data)
    messages: List[Dict[str, Any]] = []
    readings: List[str] = []
    rewritten = False
    tool_spans = 0
    unreadable: List[str] = []
    roleless = 0
    dropped: List[str] = []
    dropped_turns = 0
    saw_output = False

    for span in spans:
        attrs = _attrs(span.get("attributes"))
        op = str(attrs.get("gen_ai.operation.name") or "")

        if op == "execute_tool" or (not op and attrs.get("gen_ai.tool.name")
                                    and "gen_ai.input.messages" not in attrs):
            tool_spans += 1
            body = attrs.get("gen_ai.tool.call.result", attrs.get("gen_ai.tool.result"))
            if body is None:
                body = attrs.get("gen_ai.tool.message") or attrs.get("output.value")
            if body is None:
                continue
            text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            # The convention's recommended span name is "execute_tool {name}",
            # so the prefix is stripped: left on, the name never matches
            # --tool-returns-model-text and a final_answer tool stays a root.
            span_name = str(span.get("name") or "tool")
            if span_name.startswith("execute_tool "):
                span_name = span_name[len("execute_tool "):]
            messages.append({
                "role": "tool",
                "name": str(attrs.get("gen_ai.tool.name") or span_name),
                "tool_call_id": str(attrs.get("gen_ai.tool.call.id") or ""),
                "content": text,
            })
            continue

        incoming: List[Dict[str, Any]] = []
        if "gen_ai.input.messages" in attrs or "gen_ai.output.messages" in attrs:
            prompt_unreadable = False
            output_unreadable = False
            for key in ("gen_ai.input.messages", "gen_ai.output.messages"):
                if key in attrs and isinstance(_as_list(attrs[key]), Unreadable):
                    unreadable.append(f"{span.get('name') or 'span'}: {key}")
                    if key == "gen_ai.input.messages":
                        prompt_unreadable = True
                    else:
                        output_unreadable = True
            incoming, blank = _semconv_messages(attrs, "gen_ai.input.messages")
            roleless += blank
            if prompt_unreadable:
                # This span's prompt could not be read, so it cannot be aligned
                # against the thread. Its output is still real model text, but
                # nothing here is evidence that the history was rewritten.
                messages.extend(incoming)
            else:
                new, lost, broke = _merge(messages, incoming)
                rewritten = rewritten or broke
                dropped_turns += lost
                messages.extend(new)
            done, blank = _semconv_messages(attrs, "gen_ai.output.messages")
            roleless += blank
            if done or output_unreadable:
                # Recorded but unparseable is still recorded. Saying "no span
                # recorded model output" here would report the reader's own
                # failure as a defect in the user's export.
                saw_output = True
            messages.extend(done)
            readings.append("gen_ai.input.messages/output.messages")
            continue

        if any(k.startswith("gen_ai.prompt.") for k in attrs):
            incoming, blank = _indexed_messages(attrs, "gen_ai.prompt.", "assistant")
            roleless += blank
            new, lost, broke = _merge(messages, incoming)
            rewritten = rewritten or broke
            dropped_turns += lost
            messages.extend(new)
            done, blank = _indexed_messages(attrs, "gen_ai.completion.", "assistant")
            roleless += blank
            if done:
                saw_output = True
            messages.extend(done)
            readings.append("gen_ai.prompt.N/completion.N")
            continue

        from_choice: List[int] = []
        events = _event_messages(span, dropped, from_choice)
        if events:
            new, lost, broke = _merge(messages, events)
            rewritten = rewritten or broke
            dropped_turns += lost
            messages.extend(new)
            # A `gen_ai.assistant.message` event is replayed history, not this
            # call's output; only a choice is the model speaking now - and only
            # a choice that actually yielded a message. An empty one used to
            # suppress the warning it exists to raise.
            if from_choice:
                saw_output = True
            readings.append("gen_ai events")

    notes: List[str] = []
    if rewritten:
        notes.append(
            "a later span's prompt does not extend the earlier one, so the "
            "history was trimmed, compacted or reordered between calls. The run "
            "still reads as one thread, but a step here declares as inputs "
            "everything recorded before it, which is more than the model was "
            "actually shown")
    if dropped_turns:
        notes.append(
            f"{dropped_turns} turn(s) in a rewritten prompt repeated text already "
            f"recorded and were not recorded again, because two artifacts with "
            f"the same text cannot be told apart by a quote. If the run really "
            f"did repeat itself, those repeats are missing from this reading")
    if unreadable:
        notes.append(
            "content was recorded on these spans but could not be parsed - "
            "usually a message list cut by OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT: "
            + ", ".join(unreadable[:5])
            + (f" (+{len(unreadable) - 5} more)" if len(unreadable) > 5 else "")
            + ". What the audit cannot see here is missing from the reading, "
              "not from the run")
    if roleless:
        notes.append(
            f"{roleless} turn(s) recorded content with no role. They are read as "
            f"the model's own words, which can raise a false alarm but never "
            f"lets model text pass as evidence")
    if dropped:
        notes.append("events this reader could not place: " + ", ".join(dropped[:5]))
    if readings and not saw_output:
        notes.append(
            "no span recorded model output - every turn here comes from a prompt. "
            "What this reading calls the answer is the last prompt turn, which is "
            "not an answer at all: there is nothing to audit back from")
    if len(set(readings)) > 1:
        notes.append("this export mixes conventions: " + ", ".join(sorted(set(readings))))

    meta = {
        "spans": len(spans),
        "tool_spans": tool_spans,
        "reading": sorted(set(readings)) or ["none"],
        "unreadable_attributes": unreadable,
        "roleless_turns": roleless,
        "dropped_repeat_turns": dropped_turns,
        "recorded_model_output": saw_output,
        "notes": notes,
    }
    return messages, meta


def to_trace(data: Any, *, name: str = "",
             max_tool_chars: int = DEFAULT_MAX_TOOL_CHARS,
             model_text_tools: Optional[Iterable[str]] = None,
             verbatim_tools: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Return a raw trace dict for a GenAI span export."""
    messages, span_meta = to_messages(data)
    if not messages:
        raise ValueError(
            "no content recorded on these spans: the GenAI conventions make "
            "prompt and completion content Opt-In, and this export did not "
            "enable it. There is nothing for an audit to read - turn content "
            "capture on in your instrumentation (for the OpenTelemetry Python "
            "SDK, OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=span_and_event; "
            "the older value `true` records content on events only, which this "
            "reader can still read but less precisely) and record the run again")
    trace = _messages_to_trace(messages, name=name, max_tool_chars=max_tool_chars,
                               model_text_tools=model_text_tools,
                               verbatim_tools=verbatim_tools)
    meta = trace["_meta"]
    meta["source"] = "otel-genai"
    meta["otel"] = span_meta
    meta["reader_confidence"] = (
        "read against the OpenTelemetry GenAI specification and logs built to "
        "match it, not against a corpus of real exports")
    return trace
