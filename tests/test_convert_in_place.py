"""The trace-size line when `convert` writes over the log it read.

`convert log.json -o log.json` overwrites the log with the trace, and only then
measures "the log on disk" - by the same path. The line then names a log the
size of the trace, a size no log ever had. 23908ba, 300-turn chat of 122,317
bytes: "the trace is 2.9 MB from a log of 2.9 MB on disk (1x)".
"""

from __future__ import annotations

import json

from tallystick.cli import main


def _chat(turns: int):
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
    for i in range(turns):
        m.append({"role": "assistant", "content": f"step {i}", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "lookup", "arguments": json.dumps({"k": i})}}]})
        m.append({"role": "tool", "tool_call_id": f"c{i}", "name": "lookup",
                  "content": f"result number {i * 7919}"})
    m.append({"role": "assistant", "content": "done"})
    return {"messages": m}


def _amount(n: int) -> str:
    return f"{n / 1048576:,.1f} MB" if n >= 1048576 else f"{n / 1024:,.0f} KB"


def test_convert_in_place_names_the_log_size_the_log_had(tmp_path, capsys):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_chat(300), indent=2), encoding="utf-8")
    log_size = path.stat().st_size
    assert main(["convert", str(path), "-o", str(path), "--quiet"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "trace size:" in out                      # the line is printed at all
    assert "on disk" in out, out                     # and it names the log on disk
    if "on disk" in out:
        assert f"from a log of {_amount(log_size)} on disk" in out, out
