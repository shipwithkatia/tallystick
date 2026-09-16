"""proverka13, section 3: the echo rule, accidental bypasses.

Both are a note the model wrote, read back by an ordinary tool, and kept as
evidence with no demotion and no warning - `check-trace` exits 0.

1. The reply is JSON written by Python's `json.dumps` with its default
   ensure_ascii=True, and carries more than one value (`{"id": 1, "text": ...}`).
   Non-Latin text then arrives as \\uXXXX. The demotion unescapes the reply but
   asks only about single-value JSON; the warning path (`_share_in`) weighs the
   reply's words as written, escapes and all, so no word matches.
2. CJK text has no spaces, so the whole note is one whitespace "word". Any mark
   glued to it - the quote of a pretty-printed JSON value, a fullwidth label
   `笔记：`, Japanese 「」 quotes - makes that one word match nothing.
"""
import json

import pytest

from tallystick.adapters.openai_chat import to_trace
from tallystick.cli import main

RU = ("Население Канберры в 2021 году составляло 431 тысячу человек, а город "
      "основан в 1913 году как компромисс между Сиднеем и Мельбурном.")
PL = ("Ludność Canberry w 2021 roku wynosiła 431 tysięcy, a miasto założono w "
      "1913 roku jako kompromis między Sydney a Melbourne.")
VI = ("Dân số Canberra năm 2021 là 431 nghìn người, và thành phố được thành lập "
      "năm 1913 như một sự thỏa hiệp giữa Sydney và Melbourne.")
ZH = "堪培拉2021年人口为43.1万，该城市于1913年作为悉尼和墨尔本之间的折衷方案而建立。"
JA = "キャンベラの人口は2021年に43万1千人で、1913年にシドニーとメルボルンの妥協案として建設された"


def _cross_turn(note, reply):
    """save_note in one turn, read_note in the next hands the note back."""
    return [
        {"role": "user", "content": "Tell me about Canberra."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "save_note",
                "arguments": json.dumps({"text": note}, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "saved"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c2", "type": "function", "function": {
                "name": "read_note", "arguments": json.dumps({"id": 1})}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": reply},
        {"role": "assistant", "content": note},
    ]


def _same_turn(note, reply):
    """save_note answers with the stored record."""
    return [
        {"role": "user", "content": "Tell me about Canberra."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "save_note",
                "arguments": json.dumps({"text": note}, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": reply},
        {"role": "assistant", "content": note},
    ]


def _assert_not_silent(chat, tmp_path):
    trace = to_trace(chat)
    meta = trace["_meta"]
    echo_result = [a for a in trace["artifacts"] if a["artifact_id"].startswith("t")][-1]
    demoted = echo_result["kind"] == "intermediate"
    warned = bool(meta["echo_warning_details"])
    path = tmp_path / "log.json"
    path.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
    code = main(["check-trace", str(path), "--quiet"])
    assert demoted or warned, (
        f"the model's own note came back as {echo_result['kind']} with no warning; "
        f"check-trace exit {code}")
    assert code != 0


# --- 1. \uXXXX escapes from json.dumps(ensure_ascii=True) -------------------------

@pytest.mark.parametrize("note", [RU, PL, VI, ZH], ids=["ru", "pl", "vi", "zh"])
def test_note_read_back_as_python_json_in_a_later_turn(note, tmp_path):
    _assert_not_silent(_cross_turn(note, json.dumps({"id": 1, "text": note})), tmp_path)


@pytest.mark.parametrize("note", [RU, ZH], ids=["ru", "zh"])
def test_note_saved_and_returned_as_python_json_in_the_same_call(note, tmp_path):
    _assert_not_silent(_same_turn(note, json.dumps({"id": 1, "text": note})), tmp_path)


# --- 2. CJK: the whole note is one whitespace token ------------------------------

@pytest.mark.parametrize("reply", [
    json.dumps({"id": 1, "text": ZH}, ensure_ascii=False, indent=2),
    "笔记：" + ZH,
], ids=["pretty_json_utf8", "fullwidth_label"])
def test_chinese_note_read_back_in_a_later_turn(reply, tmp_path):
    _assert_not_silent(_cross_turn(ZH, reply), tmp_path)


@pytest.mark.parametrize("reply", ["メモ：" + JA, "「" + JA + "」"],
                         ids=["fullwidth_label", "corner_quotes"])
def test_japanese_note_read_back_in_a_later_turn(reply, tmp_path):
    _assert_not_silent(_cross_turn(JA, reply), tmp_path)


def test_chinese_note_same_call_with_label(tmp_path):
    _assert_not_silent(_same_turn(ZH, "已保存：" + ZH), tmp_path)


def test_the_laundered_chinese_note_audits_clean_end_to_end(tmp_path):
    """The model's own note, read back as pretty-printed UTF-8 JSON, becomes the
    root that grounds the answer: the posted trace exits 0."""
    chat = _cross_turn(ZH, json.dumps({"id": 1, "text": ZH}, ensure_ascii=False, indent=2))
    trace = to_trace(chat)
    arts = {a["artifact_id"]: a for a in trace["artifacts"]}
    final = next(a for a in trace["artifacts"] if a["kind"] == "final_answer")
    s = arts["t4"]["content"].index(ZH)
    trace["claims"] = [{"claim_id": "c1", "artifact_id": final["artifact_id"],
                        "start": 0, "end": len(ZH)}]
    trace["entries"] = [{"entry_id": "e1", "claim_id": "c1",
                         "account": f"EVIDENCE:t4#{s}-{s + len(ZH)}", "quoted_span": ZH}]
    path = tmp_path / "posted.json"
    path.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
    assert main(["audit", str(path), "--quiet"]) != 0
