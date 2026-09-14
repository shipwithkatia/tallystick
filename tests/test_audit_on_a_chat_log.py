"""`tallystick audit` on a chat log says what the file is and what it needs.

A person with only an OpenAI chat log runs the default command and got
`cannot audit this trace: trace must be an object, got list`, exit 2. True, and
the end of the road for them. The refusal stays exit 2 - nothing was audited -
but the message now says the file looks like a chat log rather than a
tallystick trace, names the shape `audit` expects (an object with `artifacts`
and `steps`), and says where the format is described.
"""

from __future__ import annotations

import json

import pytest

from tallystick.cli import main

CHAT = [{"role": "user", "content": "What is the capital of Australia?"},
        {"role": "assistant", "content": "Canberra."}]


def _audit(tmp_path, capsys, data, *argv):
    path = tmp_path / "log.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    code = main([*argv, str(path)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("data", [
    pytest.param(CHAT, id="bare-message-list"),
    pytest.param({"messages": CHAT}, id="object-with-messages"),
])
@pytest.mark.parametrize("argv", [pytest.param(["audit"], id="audit"),
                                  pytest.param([], id="default-command")])
def test_audit_on_a_chat_log_says_what_it_is_and_what_is_expected(tmp_path, capsys, data, argv):
    code, out, err = _audit(tmp_path, capsys, data, *argv)
    assert code == 2
    assert out == ""
    text = err.lower()
    assert "chat log" in text, err
    assert "not a tallystick trace" in text, err
    assert "artifacts" in err and "steps" in err, err
    assert "docs/auditable-traces.md" in err, err


def test_a_malformed_trace_keeps_its_precise_error_and_is_not_called_a_chat_log(tmp_path, capsys):
    code, _out, err = _audit(tmp_path, capsys, {"artifacts": [{"kind": "document"}], "steps": []},
                             "audit")
    assert code == 2
    assert "artifact_id" in err
    assert "chat log" not in err.lower()


def test_a_list_that_is_not_a_chat_log_is_not_called_one(tmp_path, capsys):
    code, _out, err = _audit(tmp_path, capsys, [1, 2, 3], "audit")
    assert code == 2
    assert "chat log" not in err.lower()
    assert "artifacts" in err and "steps" in err
