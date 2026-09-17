"""Round 22: how deep a tool call's arguments may nest is the reader's own rule,
not the interpreter's.

Round 21 made arguments nested 1,500 deep exit 2, and the test for it passed on
Python 3.12 and 3.14 only. On 3.10 the same log was read, exit 0: its JSON
parser gives up at about a thousand levels, the reader took that as "not JSON"
and read the arguments as text. On 3.12 the parser goes deeper, and the
recursive walk after it is what gave up. The same log, two answers, decided by
the Python a user happens to run.

Now the reader counts the nesting itself, in one pass with no recursion, before
anything parses the arguments: deeper than MAX_ARGUMENT_NESTING (256) is refused
with exit 2 on every version. Every case here is a small example written in this
file (rule 13), and the limit is written here as a number, so that a change to
it is a change someone has to make on purpose.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tallystick.adapters import openai_chat
from tallystick.cli import main

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 256
SENTENCE = "nested more deeply than this reader can follow"


def _log(arguments):
    return [{"role": "user", "content": "q"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "run", "arguments": arguments}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok done here"},
            {"role": "assistant", "content": "answer text"}]


def _nested(depth):
    return "[" * depth + '"x"' + "]" * depth


def _run(tmp_path, command, log, capsys):
    path = tmp_path / "log.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    argv = [command, str(path)] + (["-o", str(tmp_path / "t.json")] if command == "convert"
                                   else ["--quiet"])
    capsys.readouterr()
    code = main(argv)
    return code, capsys.readouterr().err


def test_the_limit_is_256():
    assert getattr(openai_chat, "MAX_ARGUMENT_NESTING", None) == LIMIT


@pytest.mark.parametrize("command", ["check-trace", "convert"])
def test_arguments_nested_exactly_to_the_limit_are_read(tmp_path, capsys, command):
    code, err = _run(tmp_path, command, _log(_nested(LIMIT)), capsys)
    assert code == 0 and SENTENCE not in err, err[-300:]


@pytest.mark.parametrize("depth", [LIMIT + 1, 1500, 20000])
@pytest.mark.parametrize("command", ["check-trace", "convert"])
def test_arguments_nested_past_the_limit_are_exit_2_on_every_python(tmp_path, capsys,
                                                                   command, depth):
    """257 is below where any supported Python fails; 1,500 is where 3.10 used to
    read and 3.12 refuse; 20,000 is past 3.12's parser, which read it again."""
    code, err = _run(tmp_path, command, _log(_nested(depth)), capsys)
    assert code == 2 and SENTENCE in err, (code, err[-300:])


@pytest.mark.parametrize("command", ["check-trace", "convert"])
def test_arguments_given_as_an_object_are_held_to_the_same_limit(tmp_path, capsys, command):
    """The Anthropic shape: arguments as an object, not a JSON string."""
    obj = "x"
    for _ in range(LIMIT + 1):
        obj = {"k": obj}
    code, err = _run(tmp_path, command, _log(obj), capsys)
    assert code == 2 and SENTENCE in err, (code, err[-300:])


@pytest.mark.parametrize("text", [
    "[" * 1000,                           # brackets inside a string are text
    '\\"' + "[" * 1000,                  # an escaped quote does not end the string
    "\\\\" + "{" * 1000,                  # an escaped backslash does not escape the quote
], ids=["plain", "escaped_quote", "escaped_backslash"])
def test_brackets_inside_a_string_are_not_nesting(tmp_path, capsys, text):
    """Code handed to an interpreter is full of brackets; they sit in a string."""
    arguments = '{"code": "' + text + '"}'
    json.loads(arguments)                 # the example itself is valid JSON
    code, err = _run(tmp_path, "check-trace", _log(arguments), capsys)
    assert code == 0 and SENTENCE not in err, (code, err[-300:])


def test_nesting_after_a_closed_string_with_an_escape_is_counted(tmp_path, capsys):
    """An escape is one character: the string ends at its quote, and what comes
    after it is counted again."""
    arguments = '{"note": "line one\\nline two", "rows": ' + _nested(LIMIT + 1) + "}"
    json.loads(arguments)
    code, err = _run(tmp_path, "check-trace", _log(arguments), capsys)
    assert code == 2 and SENTENCE in err, (code, err[-300:])


def test_many_lists_side_by_side_are_not_nesting(tmp_path, capsys):
    """A table of 1,000 rows is two levels deep, not a thousand."""
    arguments = json.dumps({"rows": [[k, str(k)] for k in range(1000)]})
    code, err = _run(tmp_path, "check-trace", _log(arguments), capsys)
    assert code == 0 and SENTENCE not in err, (code, err[-300:])


def test_arguments_that_are_not_json_are_still_read_as_text(tmp_path, capsys):
    """Code arguments (CodeAct) are not JSON and are counted the same way; code
    under the limit is read as before, brackets left open included."""
    code, err = _run(tmp_path, "check-trace", _log("print(" + "[" * 10 + ")"), capsys)
    assert code == 0, err[-300:]


def test_the_python_api_raises_value_error_not_recursion_error():
    with pytest.raises(ValueError, match=SENTENCE):
        openai_chat.to_trace(_log(_nested(1500)))


def test_a_real_process_exits_2_with_no_traceback(tmp_path):
    """As a terminal or CI job runs it, with the interpreter's own stack."""
    path = tmp_path / "log.json"
    path.write_text(json.dumps(_log(_nested(1500))), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, "-m", "tallystick.cli", "check-trace", str(path),
                           "--quiet"], cwd=tmp_path, env=env, capture_output=True, text=True,
                          timeout=300)
    assert done.returncode == 2 and "Traceback" not in done.stderr, done.stderr[-300:]
