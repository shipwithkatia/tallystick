"""What the reader warns about, after the warnings were sampled and counted.

The seventh round shipped two warning triggers on the answering call: an exact
value of the call standing anywhere inside a longer reply, and a share of the
reply covered by the call's own text. The eighth round drew 20 random
trajectories for each and classified them by hand (see the round's report):

    exact value inside a longer reply   1 of 10 were real echoes
                                        (first written down here as 0; a
                                        later draw found a tweet the model
                                        wrote, handed back - round 10)
    coverage >= 30%                     9 of 20
    coverage >= 50%                    12 of 20

and the acceptance test named before the measurement was "more than half of
the warnings are real echoes, at no more than 20% of the corpus". Only
coverage >= 50% passes it, at 106 of 693 trajectories (15.3%). So:

- the exact-value-inside trigger is gone. It cost 300 of 693 trajectories
  (43.3%) and, in the sample, flagged a search repeating its query, an invoice
  looked up by the id it was given, a browser reporting the URL it opened, an
  interpreter whose reply merely contained the word `False`;
- what remains is one trigger with one number in it, and the same number is
  used on BOTH paths - the call a result answers, and the calls of earlier
  turns, where the seventh round left the old "more than half" reading and the
  sixth round's three-character trick still walked through.
"""

from __future__ import annotations

import json

import pytest

from tallystick.adapters import openai_chat as oc

QUESTION = {"role": "user", "content": "go"}
NOTE = ("The Australian Bureau of Statistics recorded that the resident population "
        "of the Canberra region reached four hundred and sixty two thousand people "
        "in the June quarter, an increase of one point nine per cent on the year.")
HALF = len(NOTE) // 2
#: The sixth review's bypass: a newline in the middle, a one-character line after.
BROKEN = NOTE[:HALF] + "\n" + NOTE[HALF:] + "\n0"


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name,
                         "arguments": args if isinstance(args, str) else json.dumps(args)}}


def _cross_turn_log(readback):
    """A note saved in one turn and read back in the next - two different tools,
    so nothing the answering-call rule can see."""
    return [QUESTION,
            {"role": "assistant", "content": "save",
             "tool_calls": [_call("save_note", {"note": NOTE}, "c0")]},
            {"role": "tool", "tool_call_id": "c0", "name": "save_note", "content": "ok"},
            {"role": "assistant", "content": "read it back",
             "tool_calls": [_call("read_note", {"key": "n1"}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "read_note", "content": readback},
            {"role": "assistant", "content": "done"}]


def _same_turn_log(args, result):
    return [QUESTION,
            {"role": "assistant", "content": "working",
             "tool_calls": [_call("notes", args, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "notes", "content": result},
            {"role": "assistant", "content": "done"}]


def _read(messages, **kw):
    trace = oc.to_trace(messages, **kw)
    art = next(a for a in trace["artifacts"] if a["artifact_id"].startswith("t")
               and a["content"].strip() not in ("ok",))
    meta = trace["_meta"]
    return {"kind": art["kind"], "warnings": meta["echo_warning_details"],
            "demoted": bool(meta["echoed_back_tool_results"]), "meta": meta}


def _noticed(read):
    return read["demoted"] or bool(read["warnings"])


# --- the mandatory check: the same trick on the cross-turn path --------------

@pytest.mark.parametrize("readback, why", [
    pytest.param(BROKEN, "a newline in the middle and a one-character line after it",
                 id="X1-the-three-character-bypass"),
    pytest.param(NOTE[:HALF] + "\n" + NOTE[HALF:] + "\n[ok]",
                 "a newline in the middle and a short status line after it",
                 id="X2-newline-and-a-status-line"),
    pytest.param("=" * 220 + "\n" + NOTE, "diluted below half by a banner",
                 id="X3-diluted-below-half"),
])
def test_a_note_read_back_broken_up_is_not_silent(readback, why):
    read = _read(_cross_turn_log(readback))
    assert _noticed(read), (
        f"{why}: a note the model wrote in an earlier turn came back and the "
        f"reading passed it as evidence with no warning (kind={read['kind']})")


def test_control_a_note_read_back_whole_is_still_warned():
    read = _read(_cross_turn_log(NOTE))
    assert _noticed(read)


# --- one number, and the same one on both paths -----------------------------

def _padded(share):
    """A reply that is `share` of the note by letters and digits, with the note
    never the last line, so only coverage can see it."""
    want = oc._alnum(NOTE)
    pad = max(1, round(want / share) - want)
    return NOTE[:HALF] + "\n" + NOTE[HALF:] + "\n" + "x" * pad


@pytest.mark.parametrize("log_of", [_same_turn_log, None], ids=["answering-call", "cross-turn"])
def test_the_same_share_decides_on_both_paths(log_of):
    def read(share):
        body = _padded(share)
        if log_of is None:
            return _read(_cross_turn_log(body))
        return _read(_same_turn_log({"note": NOTE}, body))
    assert _noticed(read(0.70)), "70% of the reply is the model's text and nothing was said"
    assert not _noticed(read(0.20)), "20% of the reply is the model's text and it was flagged"


def test_the_threshold_is_one_named_constant():
    assert oc.WARN_SHARE == 0.50, (
        "the share was chosen by sampling 20 warnings at 30% and at 50%; "
        "changing it without repeating that sampling is guessing")


# --- what no longer warns, and why ------------------------------------------

@pytest.mark.parametrize("args, result, why", [
    pytest.param({"booking_id": "3426812", "insurance_id": "498276044"},
                 json.dumps({"invoice": {"booking_id": "3426812", "travel_date": "2024-11-12",
                                         "travel_from": "CRH", "travel_to": "JFK",
                                         "travel_cost": 300.0, "transaction_id": "45451592"}}),
                 "an invoice looked up by the id it was given", id="F1-invoice-by-id"),
    pytest.param({"url": "https://example.org/a/very/long/path/to/an/article/page"},
                 "Error fetching the webpage: 403 Client Error: Forbidden for url: "
                 "https://example.org/a/very/long/path/to/an/article/page",
                 "a real error naming the URL it was handed", id="F2-403-on-the-url"),
    pytest.param({"stock": "ZETA"}, json.dumps({"symbol": ["NVDA", "ZETA"]}),
                 "a watchlist that now holds the symbol, and another", id="F3-watchlist"),
    pytest.param({"receiver_id": "BD732D1888B94DAA", "message": "I am on my way."},
                 json.dumps({"sent_status": True, "message_id": {"new_id": 67410},
                             "message": "Message sent to 'BD732D1888B94DAA' successfully."}),
                 "a send confirmed with a real message id", id="F4-send-confirmation"),
])
def test_a_tool_doing_real_work_is_not_flagged(args, result, why):
    # Every one of these is from the corpus sample, and every one was classified
    # by hand as the tool doing its job. They used to warn because a value of
    # the call stood somewhere inside the reply.
    read = _read(_same_turn_log(args, result))
    assert not _noticed(read), f"{why}: flagged (kind={read['kind']})"


def test_a_value_handed_straight_back_is_still_demoted():
    # The demotion is untouched by this round: the reply IS the value.
    read = _read(_same_turn_log({"text": NOTE}, json.dumps({"text": NOTE})))
    assert read["demoted"], read["kind"]


def test_a_bare_word_of_an_earlier_call_is_not_a_value_across_turns():
    # Across calls nothing ties a reply to a call, so the values weighed there
    # are the ones a call plainly carried - its JSON values, its literals, its
    # lines. Not its bare word atoms: in the round's sample, every atom match
    # across calls was the tool doing its job (a reply holding the word
    # `False`, a query term, a file name).
    log = [QUESTION,
           {"role": "assistant", "content": "compute",
            "tool_calls": [_call("python", {"code": "results = compute(dataset)\nprint(len(results))"}, "c0")]},
           {"role": "tool", "tool_call_id": "c0", "name": "python", "content": "412"},
           {"role": "assistant", "content": "now name it",
            "tool_calls": [_call("lookup", {"id": 7}, "c1")]},
           {"role": "tool", "tool_call_id": "c1", "name": "lookup", "content": "dataset"},
           {"role": "assistant", "content": "done"}]
    trace = oc.to_trace(log)
    kinds = {w["kind"] for w in trace["_meta"]["echo_warning_details"]}
    assert "earlier_turn" not in kinds, trace["_meta"]["echo_warning_details"]


def test_a_repeated_word_does_not_make_the_reading_quadratic():
    # A word that stands in thousands of places is a word of the language. Runs
    # are not started from it; without that, one turn of a long run costs the
    # square of its own length.
    import time
    word = "value"
    args = json.dumps({"code": " ".join([word] * 20000)})
    reply = " ".join([word] * 4000)
    start = time.perf_counter()
    oc.to_trace(_same_turn_log(args, reply))
    seconds = time.perf_counter() - start
    assert seconds < 5.0, f"reading one turn of repeated words took {seconds:.1f}s"
