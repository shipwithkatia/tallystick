"""Round 15: the edges of the echo fix that the review's tests do not reach.

The review's tests (test_proverka13_echo.py) say a Russian note read back as
`json.dumps` writes it, and a Chinese or Japanese note behind a quote, a label
or 「」, must not pass in silence. They do not say what the fix must NOT do, and
two wider fixes pass them too:

* cutting words at every mark in every script - on AgentHallu it added more
  trajectories needing a person than the limit named for it, and a JSON key
  such as `current_time` matched the model's words `current time`;
* one character per word in scripts without spaces - on a long Chinese chat
  with no echo in it, the reading ran out of work and reported `unchecked`
  warnings, each exit 1.

And a named hole stays a hole: a reply longer than UNESCAPE_RESULT_CHARS is not
decoded, on either path.
"""

from __future__ import annotations

import json
import random

from tallystick import TraceError, audit
from tallystick.adapters.openai_chat import UNESCAPE_RESULT_CHARS, to_trace
from tallystick.cli import main

import pytest


def _call(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _cross_turn(note, reply, reader="read_note", reader_args=None):
    return [
        {"role": "user", "content": "Tell me about Canberra."},
        {"role": "assistant", "content": None, "tool_calls": [_call("c1", "save_note", {"text": note})]},
        {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "saved"},
        {"role": "assistant", "content": None,
         "tool_calls": [_call("c2", reader, reader_args or {"id": 1})]},
        {"role": "tool", "tool_call_id": "c2", "name": reader, "content": reply},
        {"role": "assistant", "content": "done"},
    ]


def _warned(chat):
    return [w["kind"] for w in to_trace(chat)["_meta"]["echo_warning_details"]]


# --- scripts without spaces, beyond Chinese and Japanese -------------------------

TH = ("ประชากรของแคนเบอร์ราในปี2021มีจำนวน431000คนและเมืองนี้ก่อตั้งในปี1913"
      "เพื่อเป็นการประนีประนอมระหว่างซิดนีย์และเมลเบิร์น")


@pytest.mark.parametrize("reply", [
    json.dumps({"id": 1, "text": TH}, ensure_ascii=False, indent=2),
    "บันทึก：「" + TH + "」",
], ids=["pretty_json", "label_and_quotes"])
def test_a_thai_note_read_back_is_reported(reply):
    assert _warned(_cross_turn(TH, reply)) == ["earlier_turn"]


# --- what the fix must not reach -------------------------------------------------

def test_a_json_key_spelling_the_models_words_is_not_an_echo():
    """`{"current_time": "10:30 AM"}` after the model asked about "the current
    time": cut at every mark, `current_time` becomes the words `current time`,
    11 of the reply's 17 letters and digits, and the reply warns."""
    chat = _cross_turn("What is the current time in Tokyo?", json.dumps({"current_time": "10:30 AM"}),
                       reader="get_current_time", reader_args={})
    assert _warned(chat) == []


def test_a_status_line_naming_the_file_it_moved_is_not_an_echo():
    """`mv` answering `'final_report.pdf' moved to 'temp/final_report.pdf'` -
    a tool doing its job, read by hand among the warnings that version added."""
    chat = [
        {"role": "user", "content": "Move the report."},
        {"role": "assistant", "content": None, "tool_calls": [
            _call("c1", "mv", {"source": "final_report.pdf", "destination": "temp"})]},
        {"role": "tool", "tool_call_id": "c1", "name": "mv",
         "content": json.dumps({"result": "'final_report.pdf' moved to 'temp/final_report.pdf'"})},
        {"role": "assistant", "content": "done"},
    ]
    assert _warned(chat) == []


def test_a_long_chinese_chat_with_no_echo_is_weighed_to_the_end():
    """100 turns: the model writes 1,000 Han characters into each call, the tool
    answers with 2,000 others, drawn from the same 400 characters and never in
    the same order. One character per word, the index holds every character
    thousands of times, the exact pass runs out of work and 43 replies come back
    `unchecked` - exit 1 on a run with no echo in it."""
    rng = random.Random(15)
    han = [chr(c) for c in range(0x4E00, 0x4E00 + 400)]
    chat = [{"role": "user", "content": "请调查。"}]
    for i in range(100):
        query = "".join(rng.choice(han) for _ in range(1000))
        chat.append({"role": "assistant", "content": None,
                     "tool_calls": [_call(f"c{i}", "web_search", {"query": query})]})
        chat.append({"role": "tool", "tool_call_id": f"c{i}", "name": "web_search",
                     "content": "".join(rng.choice(han) for _ in range(2000))})
    chat.append({"role": "assistant", "content": "完成。"})
    assert _warned(chat) == []


# --- the hole that stays, named ------------------------------------------------

RU = ("Население Канберры в 2021 году составляло 431 тысячу человек, а город "
      "основан в 1913 году как компромисс между Сиднеем и Мельбурном.")


def test_an_escaped_note_is_weighed_decoded_up_to_the_decoding_limit():
    reply = json.dumps({"id": 1, "text": RU})               # \\uXXXX, short
    assert len(reply) <= UNESCAPE_RESULT_CHARS
    assert _warned(_cross_turn(RU, reply)) == ["earlier_turn"]


def test_past_the_decoding_limit_an_escaped_note_is_not_seen():
    """README: replies longer than 2,000 characters are not decoded, on either
    path. If this starts to warn, the limit moved and the README is wrong."""
    note = " ".join([RU] * 5)
    reply = json.dumps({"id": 1, "text": note})
    assert len(reply) > UNESCAPE_RESULT_CHARS
    assert _warned(_cross_turn(note, reply)) == []


def test_the_share_is_the_larger_of_the_two_spellings():
    """Below WARN_SHARE the share is not acted on, so no end-to-end test sees
    which spelling it came from; the function still says it returns the share.
    Padding, then six words of the note escaped: weighed as written the escaped
    words match nothing, decoded they do."""
    from tallystick.adapters.openai_chat import _arg_words, _share_in, _unescape
    words = " ".join(RU.split()[:6])
    reply = "Nothing to see here at all, only padding words " + json.dumps(words)
    index = _arg_words(json.dumps({"text": RU}, ensure_ascii=False))
    decoded, _run, _unsure = _share_in(_unescape(reply), index)
    share, _run, _unsure = _share_in(reply, index)
    assert 0 < decoded < 0.5
    assert share == decoded


# --- unreadable paths, one measure for the command and the function ---------------

@pytest.mark.parametrize("make", ["missing", "directory"])
def test_an_unreadable_path_is_exit_2_and_trace_error_alike(tmp_path, make, capsys):
    path = tmp_path / "run.json"
    if make == "directory":
        path.mkdir()
    with pytest.raises(TraceError, match="cannot be read"):
        audit(str(path))
    assert main(["audit", str(path), "--quiet"]) == 2
    assert main(["check-trace", str(path), "--quiet"]) == 2
    assert main(["convert", str(path), "-o", str(tmp_path / "out.json"), "--quiet"]) == 2
    assert "cannot be read" in capsys.readouterr().err


# --- convert in place ------------------------------------------------------------

def test_convert_to_another_path_still_names_the_logs_size(tmp_path, capsys):
    """The size is now taken before the write; writing elsewhere must still
    report the log as it lies on disk."""
    m = [{"role": "user", "content": "q"}]
    for i in range(300):
        m.append({"role": "assistant", "content": f"step {i}",
                  "tool_calls": [_call(f"c{i}", "lookup", {"k": i})]})
        m.append({"role": "tool", "tool_call_id": f"c{i}", "name": "lookup", "content": f"r{i}"})
    m.append({"role": "assistant", "content": "done"})
    log = tmp_path / "run.json"
    log.write_text(json.dumps({"messages": m}), encoding="utf-8")
    size = log.stat().st_size
    assert main(["convert", str(log), "-o", str(tmp_path / "t.json"), "--quiet"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert f"from a log of {size / 1024:,.0f} KB on disk" in out, out
