"""What the reader does with a reply that holds the call's own values.

The eighth round sampled the warning triggers of the seventh by hand, kept one
(a share of the reply covered by call text) and dropped the other (a value of
the call standing anywhere inside a longer reply: 1 of 10 real echoes). Round
18 removed the remaining warnings too, after review 16 drew 20 of them and
found 6 real echoes (see tallystick/echo_gate.py). The tests that guarded the
warnings went with them; what stays here guards the demotion on both sides:
a tool doing real work is NOT read as the model's text, and a reply that IS the
value it was given still is.
"""

from __future__ import annotations

import json

import pytest

from tallystick.adapters import openai_chat as oc

QUESTION = {"role": "user", "content": "go"}
NOTE = ("The Australian Bureau of Statistics recorded that the resident population "
        "of the Canberra region reached four hundred and sixty two thousand people "
        "in the June quarter, an increase of one point nine per cent on the year.")


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name,
                         "arguments": args if isinstance(args, str) else json.dumps(args)}}


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
    return {"kind": art["kind"],
            "demoted": bool(meta["echoed_back_tool_results"]), "meta": meta}


# --- what is not read as the model's text, and what is ---------------------

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
    # by hand as the tool doing its job. A value of the call stands somewhere
    # inside the reply; the reply is not that value, so it stays evidence.
    read = _read(_same_turn_log(args, result))
    assert not read["demoted"] and read["kind"] == "tool_result", f"{why}: demoted"


def test_a_value_handed_straight_back_is_still_demoted():
    # The demotion is untouched by this round: the reply IS the value.
    read = _read(_same_turn_log({"text": NOTE}, json.dumps({"text": NOTE})))
    assert read["demoted"], read["kind"]


def test_a_repeated_word_does_not_make_the_reading_quadratic():
    # A word that stands in thousands of places: reading one such turn must not
    # cost the square of its length.
    import time
    word = "value"
    args = json.dumps({"code": " ".join([word] * 20000)})
    reply = " ".join([word] * 4000)
    start = time.perf_counter()
    oc.to_trace(_same_turn_log(args, reply))
    seconds = time.perf_counter() - start
    assert seconds < 5.0, f"reading one turn of repeated words took {seconds:.1f}s"
