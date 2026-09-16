"""Round 15: exit codes for unreadable paths, and convert in place.

The rest of this file guarded the edges of the round-15 echo warning fix -
scripts without spaces, what the word cut must not reach, a long Chinese chat,
the decoding limit, the share of two spellings - and was removed with the
warnings in round 18.
"""

from __future__ import annotations

import json

from tallystick import TraceError, audit
from tallystick.cli import main

import pytest


def _call(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


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
