"""proverka20, 2.4a: a note in a script written without spaces that is not on
the reader's list (_NO_SPACES) is one whitespace word, so a quote, a label or a
bracket glued to it makes it match nothing. The same shape in English, Chinese
and Thai is noted; here nothing is said. Old: e69a6bc behaves the same."""
import json

import pytest

from tallystick.adapters.openai_chat import to_trace
from tallystick.echo_gate import echo_warnings

# Javanese letters and vowel signs (U+A984-U+A9BF), written as Javanese is:
# no spaces between words.
JV = ("ꦲꦏꦸꦱꦶꦤꦮꦸꦭꦁꦏꦸꦏꦶꦠꦥ꧀ꦩꦚꦁꦥꦱꦂꦏꦼꦤꦝꦺꦴꦁꦏꦸꦭꦺꦴꦤ꧀ꦱꦏꦶꦁ"
      "ꦥꦼꦏꦤ꧀ꦠꦸꦏꦸꦒꦸꦭꦲꦤ꧀ꦭꦤ꧀ꦧꦺꦴꦠꦺꦴꦤ꧀")
YI = "ꀀꀁꀂꀃꀄꀅꀆꀇꀈꀉꀊꀋꀌꀍꀎꀏꀐꀑꀒꀓꀔꀕꀖꀗꀘꀙꀚꀛꀜꀝꀞꀟꀠꀡꀢꀣ"
ZH = "堪培拉2021年人口为43.1万，该城市于1913年作为悉尼和墨尔本之间的折衷方案而建立。"


def _log(note, reply):
    call = lambda name, args, cid: {"id": cid, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
    return [{"role": "user", "content": "Tell me."},
            {"role": "assistant", "content": None, "tool_calls": [call("save_note", {"text": note}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "ok"},
            {"role": "assistant", "content": None, "tool_calls": [call("read_note", {"id": 1}, "c2")]},
            {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": reply},
            {"role": "assistant", "content": "done"}]


SHAPES = {"label_quotes": lambda n: f'Note: "{n}"',
          "pretty_json": lambda n: json.dumps({"id": 1, "text": n}, ensure_ascii=False, indent=2),
          "brackets": lambda n: f"[{n}]"}


@pytest.mark.parametrize("shape", list(SHAPES))
@pytest.mark.parametrize("note", [ZH, JV, YI], ids=["zh_control", "javanese", "yi"])
def test_a_note_in_an_unlisted_script_without_spaces_is_noted_like_chinese(note, shape):
    meta = to_trace(_log(note, SHAPES[shape](note)))["_meta"]
    assert echo_warnings(meta), f"the model's own note in {shape} came back with no note"
