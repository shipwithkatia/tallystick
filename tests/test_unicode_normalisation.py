"""proverka20, 2.4b: the reader compares text without Unicode normalisation,
while the verifier (normalize.py) compares in NFC. A Vietnamese or Korean note
read back decomposed (NFD - how macOS file names and some stores keep text)
shares no word with the call that wrote it: not demoted on the answering call,
not noted on a later one, and a claim quoting it balances. Letter for letter it
is the same text to a reader and to the audit. Old: f84f278 behaves the same."""
import json
import unicodedata

import pytest

from tallystick.adapters.openai_chat import to_trace
from tallystick.echo_gate import echo_warnings

VI = "Thủ đô của Úc là Sydney, được chọn năm 1901 như một sự thỏa hiệp giữa hai thành phố lớn"
KO = "호주의 수도는 시드니이며 1901년에 두 도시 사이의 타협안으로 선정되었습니다"


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _later_turn(note, reply):
    return [{"role": "user", "content": "Tell me."},
            {"role": "assistant", "content": None, "tool_calls": [_call("save_note", {"text": note}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "ok"},
            {"role": "assistant", "content": None, "tool_calls": [_call("read_note", {"id": 1}, "c2")]},
            {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": reply},
            {"role": "assistant", "content": note}]


def _same_call(note, reply):
    return [{"role": "user", "content": "Tell me."},
            {"role": "assistant", "content": None, "tool_calls": [_call("save_note", {"text": note}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": reply},
            {"role": "assistant", "content": note}]


@pytest.mark.parametrize("note", [VI, KO], ids=["vi", "ko"])
def test_a_note_read_back_decomposed_is_noted_as_when_composed(note):
    composed = to_trace(_later_turn(note, note))["_meta"]
    assert echo_warnings(composed), "control: the composed read-back is noted"
    decomposed = to_trace(_later_turn(note, unicodedata.normalize("NFD", note)))["_meta"]
    assert echo_warnings(decomposed), "the same note, decomposed, is kept as evidence with no note"


@pytest.mark.parametrize("note", [VI, KO], ids=["vi", "ko"])
def test_the_answering_call_handing_back_its_note_decomposed_is_still_demoted(note):
    kind = lambda reply: next(a["kind"] for a in to_trace(_same_call(note, reply))["artifacts"]
                              if a["artifact_id"] == "t2")
    assert kind(note) == "intermediate", "control: handed back composed, it is demoted"
    assert kind(unicodedata.normalize("NFD", note)) == "intermediate", (
        "handed back decomposed, the model's own note is a root")
