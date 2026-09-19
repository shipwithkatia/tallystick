"""Round 12: every size the trace-size line prints is one the reader can check.

Round 11 measured both sides the same way - as the JSON `convert` writes - so
that the ratio says what the READING added and does not move when someone
pretty-prints their log. That quantity stays. What was wrong is what the line
said about it: for a 67 KB log on disk it printed "109 KB", the size after
reformatting, and a person who goes looking for that number in `ls` will not
find it.

So the line now names the trace, the log as it lies on disk, and the ratio
between those two - all three checkable with `ls` - and says separately that,
measured the way tallystick writes JSON on both sides, the reading added Nx.
The trigger is unchanged, and that has a price which these tests pin: a log
stored compactly can reach a higher ratio on disk than the trigger sees and
still get no word. The author's 300-turn log is exactly that case.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tallystick import convert
from tallystick.cli import main


def _amount(n: int) -> str:
    """The rendering the line uses. Spelled out here on purpose: a test that
    repeats the format is what notices the format changing."""
    return f"{n / 1048576:,.1f} MB" if n >= 1048576 else f"{n / 1024:,.0f} KB"


def _call(cid: int):
    return {"id": f"c{cid}", "type": "function",
            "function": {"name": "search", "arguments": json.dumps({"q": f"q{cid}"})}}


def _chat(turns: int, *, thoughts: bool):
    """`thoughts=False` is the shape of the log the twelfth round was reported
    on: assistant turns that carry only a tool call, and short replies."""
    messages = [{"role": "user", "content": "start"}]
    for k in range(turns):
        turn = {"role": "assistant", "tool_calls": [_call(k)]}
        if thoughts:
            turn["content"] = f"Step {k}: looking it up."
        messages += [turn,
                     {"role": "tool", "tool_call_id": f"c{k}",
                      "content": f"result number {k} about something"}]
    messages.append({"role": "assistant", "content": "done, the answer is 42"})
    return messages


def _log(tmp_path, turns, *, thoughts=True, name="log.json"):
    """Written compact, the way an API response or a JSONL line arrives - which
    is the case where the log on disk and the log reformatted differ."""
    path = tmp_path / name
    path.write_text(json.dumps(_chat(turns, thoughts=thoughts)), encoding="utf-8")
    return path


def _convert(tmp_path, log: Path, *flags):
    out = tmp_path / "trace.json"
    code = main(["convert", "--from", "openai", str(log), "-o", str(out), *flags])
    return code, out


LOUD, QUIET_SHAPE = 300, 300


# --- what the line says ------------------------------------------------------

def test_the_line_names_the_log_as_it_lies_on_disk(tmp_path, capsys):
    log = _log(tmp_path, LOUD)
    code, out = _convert(tmp_path, log)
    assert code == 0
    text = capsys.readouterr().out
    assert "trace size:" in text, text
    on_disk = _amount(log.stat().st_size)
    reformatted = _amount(convert.json_bytes(json.loads(log.read_text())))
    assert on_disk != reformatted, "this log must be compact for the test to mean anything"
    line = [ln for ln in text.splitlines() if "trace size:" in ln][0]
    block = text.split("trace size:", 1)[1]
    assert on_disk in block, f"the log's size on disk is not in the line: {block}"
    assert reformatted not in block, (
        f"the line still shows the reformatted size {reformatted} where a person "
        f"looks for {on_disk}: {block}")
    assert _amount(out.stat().st_size) in block, block
    assert line


def test_the_line_gives_the_ratio_between_the_two_files(tmp_path, capsys):
    log = _log(tmp_path, LOUD)
    _, out = _convert(tmp_path, log)
    block = capsys.readouterr().out.split("trace size:", 1)[1]
    seen = out.stat().st_size / log.stat().st_size
    assert f"({seen:.0f}x)" in block, f"expected the on-disk ratio {seen:.0f}x in: {block}"


def test_without_a_file_the_line_says_how_it_measured(tmp_path):
    log = json.loads(_log(tmp_path, LOUD).read_text())
    trace = convert.read_any(log, source="openai")[0]
    size = convert.trace_size(log, trace)          # nothing from disk
    line = convert.trace_size_line(size)
    assert "on disk" not in line, line
    assert "as tallystick writes JSON" in line, line


# --- what the trigger does, and the price of leaving it alone ----------------

def test_a_log_that_is_not_a_file_is_not_measured_on_disk(tmp_path, capsys):
    # `tallystick convert --from openai <(gunzip -c log.json.gz)` hands the CLI
    # a path like /dev/fd/63. Measured on this machine: `is_file()` is False and
    # `st_size` is 65536 - the pipe's buffer, not the log. Reporting that as
    # "the log is 64 KB on disk" would be a number from nowhere.
    import os
    payload = json.dumps(_chat(150, thoughts=True)).encode("utf-8")
    assert len(payload) < 60000, "must fit one pipe buffer or this test deadlocks"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, payload)
    os.close(write_fd)
    try:
        code = main(["convert", "--from", "openai", f"/dev/fd/{read_fd}",
                     "-o", str(tmp_path / "trace.json"), "--quiet"])
    finally:
        os.close(read_fd)
    assert code == 0
    parts = capsys.readouterr().out.split("trace size:", 1)
    assert len(parts) == 2, "the note still prints: the trigger does not depend on the file"
    assert "on disk" not in parts[1], parts[1]


def test_the_size_of_a_pipe_is_not_reported_as_a_file():
    # The test above goes through the CLI, and by the time the CLI measures, the
    # pipe has been read to the end and reports 0 - so it cannot tell a missing
    # guard from a present one. Here the data is still in the buffer, which is
    # where `st_size` of a pipe is a number about the buffer and not about any
    # log: 4096 here, 65536 for a full one. (On a system whose pipes report 0
    # even when full, this passes either way.)
    import os

    from tallystick import cli

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"x" * 4096)
    try:
        assert cli._file_bytes(f"/dev/fd/{read_fd}") is None
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_a_size_of_zero_is_no_size(tmp_path):
    # A FIFO reports st_size 0. Zero must mean "nothing to report", not a
    # division by zero and not "0 KB on disk".
    log = json.loads(_log(tmp_path, LOUD).read_text())
    trace = convert.read_any(log, source="openai")[0]
    size = convert.trace_size(log, trace, log_disk_bytes=0)
    assert "log_disk_bytes" not in size and "disk_ratio" not in size, size
    assert "on disk" not in convert.trace_size_line(size)


def test_the_log_on_disk_never_moves_the_trigger(tmp_path):
    log = json.loads(_log(tmp_path, LOUD).read_text())
    trace = convert.read_any(log, source="openai")[0]
    plain = convert.trace_size(log, trace)
    with_disk = convert.trace_size(log, trace, log_disk_bytes=17)
    assert plain["ratio"] == with_disk["ratio"], "the file's size decided the trigger"
    assert with_disk["disk_ratio"] != with_disk["ratio"]


def test_a_compact_log_below_the_ratio_still_says_nothing(tmp_path, capsys):
    # The price of keeping the trigger as it is, named in round 12 before it was
    # measured: this log grows more than tenfold on disk and gets no word,
    # because the reading itself added less than tenfold.
    log = _log(tmp_path, QUIET_SHAPE, thoughts=False)
    code, out = _convert(tmp_path, log)
    assert code == 0
    assert "trace size:" not in capsys.readouterr().out
    seen = out.stat().st_size / log.stat().st_size
    added = convert.json_bytes(json.loads(out.read_text())) / \
        convert.json_bytes(json.loads(log.read_text()))
    assert seen > convert.TRACE_SIZE_NOTE_RATIO > added, (seen, added)


# --- the same number on every path -------------------------------------------

def test_every_path_measures_the_trace_the_same(tmp_path):
    log = _log(tmp_path, LOUD)
    _, out = _convert(tmp_path, log, "--quiet")
    report = tmp_path / "report.json"
    main(["check-trace", "--from", "openai", str(log), "--quiet", "--json", str(report)])
    from_check = json.loads(report.read_text())["reading"]["trace_size"]
    assert from_check["trace_bytes"] == out.stat().st_size, (
        "check-trace and convert -o must measure the trace the same way")
    assert from_check["log_disk_bytes"] == log.stat().st_size
    assert from_check["log_bytes"] == convert.json_bytes(json.loads(log.read_text()))


def test_the_json_carries_both_ratios(tmp_path):
    log = _log(tmp_path, LOUD)
    report = tmp_path / "report.json"
    main(["check-trace", "--from", "openai", str(log), "--quiet", "--json", str(report)])
    size = json.loads(report.read_text())["reading"]["trace_size"]
    assert abs(size["ratio"] - size["trace_bytes"] / size["log_bytes"]) < 0.01
    assert abs(size["disk_ratio"] - size["trace_bytes"] / size["log_disk_bytes"]) < 0.01
    assert size["disk_ratio"] > size["ratio"], "a compact log grows more on disk"
