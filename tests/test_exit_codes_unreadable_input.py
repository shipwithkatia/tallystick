"""Input the reader cannot read is exit 2, never the exit 1 of a verdict.

0 - balances, 1 - verdict against, 2 - could not check. The cases: two final
answers of which one has no claims, JSON nested too deep for the parser, a lone
surrogate in a tool result or in the answer, and a `_meta` field of the wrong
type. Each test fails on
23908ba. An uncaught exception leaves the interpreter with exit 1, which is the
"verdict against" code; in-process, the same defect shows as `main` raising.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tallystick.cli import main

ROOT = Path(__file__).resolve().parent.parent
BALANCED = json.loads((ROOT / "examples" / "balanced_run.json").read_text(encoding="utf-8"))


def test_audit_does_not_pass_a_final_answer_nobody_posted_claims_on(tmp_path):
    """Two final_answer artifacts; one has a claim that grounds, the other has
    no claim at all. check-trace calls this a defect (multiple_final_answers,
    exit 1); audit checks one answer and exits 0 on both. 23908ba: exit 0."""
    t = {
        "artifacts": [
            {"artifact_id": "doc", "kind": "document", "content": "Canberra is the capital."},
            {"artifact_id": "ans", "kind": "final_answer", "content": "Canberra"},
            {"artifact_id": "ans2", "kind": "final_answer",
             "content": "Sydney is the capital, and the moon is made of cheese."},
        ],
        "steps": [
            {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["doc"]},
            {"step_id": "s2", "kind": "answer", "inputs": ["doc"], "outputs": ["ans", "ans2"]},
        ],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": 8}],
        "entries": [{"entry_id": "e1", "claim_id": "c1", "account": "EVIDENCE:doc#0-8",
                     "quoted_span": "Canberra"}],
    }
    path = tmp_path / "two_answers.json"
    path.write_text(json.dumps(t), encoding="utf-8")
    assert main(["audit", str(path), "--quiet"]) != 0


@pytest.mark.parametrize("command", ["audit", "check-trace"])
def test_deeply_nested_json_is_unreadable_not_a_verdict(tmp_path, command):
    """200,000 nested brackets: valid JSON grammar, but json.loads raises
    RecursionError, which read_json_file does not turn into TraceError.
    23908ba: traceback, exit 1."""
    path = tmp_path / "deep.json"
    path.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
    assert main([command, str(path), "--quiet"]) == 2


def _surrogate_log(tmp_path) -> Path:
    """An OpenAI log whose tool result ends in a lone high surrogate, written
    as the JSON escape \\ud83d - what a UTF-16 string cut in the middle of an
    emoji serialises to. json.loads accepts it."""
    text = (ROOT / "examples" / "logs" / "clean_run.json").read_text(encoding="utf-8")
    text = text.replace("Net profit: 212 million.", "Net profit: 212 million \\ud83d")
    assert "\\ud83d" in text
    path = tmp_path / "surrogate_log.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_check_trace_on_a_lone_surrogate_does_not_exit_1(tmp_path):
    """23908ba: UnicodeEncodeError in convert.json_bytes (the trace-size
    measure), traceback, exit 1."""
    assert main(["check-trace", str(_surrogate_log(tmp_path)), "--quiet"]) in (0, 2)


def test_convert_on_a_lone_surrogate_exits_2_and_leaves_no_half_file(tmp_path):
    """`convert` has no verdict and so, by its own docstring, no exit 1.
    23908ba: UnicodeEncodeError from _write_json (not an OSError), exit 1, and
    a truncated output file of a few hundred bytes left on disk."""
    out = tmp_path / "out.json"
    code = main(["convert", str(_surrogate_log(tmp_path)), "-o", str(out), "--quiet"])
    assert code == 2
    assert not out.exists() or json.loads(out.read_text(encoding="utf-8"))


def test_audit_json_on_a_lone_surrogate_does_not_exit_1(tmp_path):
    """The balanced example with one character of the answer replaced by a
    lone surrogate audits to BOOKS BALANCE; with --json the report write raises
    UnicodeEncodeError. 23908ba: exit 1 on a run whose books balance."""
    text = json.dumps(BALANCED).replace("fell to 11.2%.", "fell to 11.2%\\ud83d")
    path = tmp_path / "t.json"
    path.write_text(text, encoding="utf-8")
    assert main(["audit", str(path), "--quiet"]) == 0          # the books do balance
    assert main(["audit", str(path), "--quiet", "--json", str(tmp_path / "o.json")]) in (0, 2)


@pytest.mark.parametrize("command,key,value", [
    ("audit", "echoes_from_earlier_turns", 5),
    ("audit", "model_text_tools", 5),
    ("check-trace", "echoes_from_earlier_turns", 5),
    ("check-trace", "otel", ["x"]),
    ("check-trace", "notes", 5),
    ("check-trace", "truncated", 5),
])
def test_a_malformed_meta_block_is_not_a_verdict(tmp_path, command, key, value):
    """A `_meta` field of the wrong type in an otherwise balanced trace.
    23908ba: TypeError / AttributeError escapes, exit 1 from the shell."""
    t = copy.deepcopy(BALANCED)
    t["_meta"] = {key: value}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(t), encoding="utf-8")
    assert main([command, str(path), "--quiet"]) in (0, 2)
