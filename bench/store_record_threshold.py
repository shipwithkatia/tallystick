"""When a note read back inside a memory store's record gets a note.

    python bench/store_record_threshold.py

No data needed. A note is saved in one turn and read back in the next inside a
mem0 search result: the record's id, hash, score and dates stand around it.
The reader notes a reply when at least half of its letters and digits stand in
text the model wrote, so a short note inside that record is not noted. The
limit is in characters, not in words; three kinds of English text show it:
short words, long words, and numbers. For each, the shortest note that gets a
note, and what happens at 20 words.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tallystick.cli import main as tallystick  # noqa: E402

TEXTS = {
    "short words": ("the cat sat on a mat and it was so big but we saw it go up to the top of "
                    "a red box in my old van at six am to see if all is ok now " * 4).split(),
    "long words": ("Unquestionably international telecommunications infrastructure investments "
                   "significantly outperformed conventional manufacturing expectations throughout "
                   "nineteenth century industrialisation " * 8).split(),
    "numbers": ("Revenue 2023 was 14.2 million EUR, up 11.8 percent; margin 23.4 percent; "
                "headcount 1,204; offices 17; churn 3.1 percent; NPS 41; ARR 9.7 million " * 4).split(),
}


def _log(note: str) -> list:
    record = json.dumps({"results": [{
        "id": "892db2ae-06d9-49e5-8b3e-585ef9b85b8e", "memory": note, "hash": "3f2b1c9e",
        "metadata": None, "score": 0.38, "created_at": "2026-09-16T10:00:00-07:00",
        "updated_at": None, "user_id": "alice"}]})
    return [{"role": "user", "content": "Remember this."},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "save_note", "arguments": json.dumps({"text": note})}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function",
                "function": {"name": "search_memory", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c2", "content": record},
            {"role": "assistant", "content": "Done."}]


def _noted(note: str, tmp: Path) -> bool:
    log, report = tmp / "log.json", tmp / "report.json"
    log.write_text(json.dumps(_log(note)), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        tallystick(["check-trace", str(log), "--quiet", "--json", str(report)])
    return json.loads(report.read_text(encoding="utf-8"))["may_be_model_text"]["count"] > 0


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    print("a note read back inside a mem0 search record")
    for name, words in TEXTS.items():
        at_20 = " ".join(words[:20])
        first = next((n for n in range(1, len(words) + 1) if _noted(" ".join(words[:n]), tmp)), None)
        shown = (f"first noted at {first} words ({len(' '.join(words[:first]))} characters)"
                 if first else "never noted")
        print(f"  {name:<12} 20 words ({len(at_20)} characters): "
              f"{'noted' if _noted(at_20, tmp) else 'not noted'}; {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
