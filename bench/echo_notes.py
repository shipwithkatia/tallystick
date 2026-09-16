"""How many AgentHallu trajectories get a note that a tool result may be the
model's own text.

    python bench/echo_notes.py DIR

DIR holds the AgentHallu trajectories, which are not in this repository:
git clone https://github.com/liuxuannan/AgentHallu. Each trajectory is rendered
as a chat log (bench/openai_roundtrip.py) and read with the corpus's four
echo-returning tools declared, as `--tool-returns-model-text` would. The notes
counted are the ones `check-trace` lists under the report and in `--json`
(`may_be_model_text`); they never change an exit code.

How many of them are right is not measured here: that needs reading them by
hand. An external review drew 20 at random and found 6 were the model's own
text (README).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from openai_roundtrip import render  # noqa: E402
from tallystick.adapters import agenthallu, openai_chat  # noqa: E402
from tallystick.echo_gate import echo_warnings  # noqa: E402


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    root = Path(argv[0])
    n = noted = notes = 0
    kinds: Counter = Counter()
    for p in sorted(root.rglob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not (isinstance(obj, dict) and "history" in obj):
            continue
        n += 1
        meta = openai_chat.to_trace(render(obj), name=p.name,
                                    model_text_tools=agenthallu.ECHO_TOOLS)["_meta"]
        found = echo_warnings(meta)
        noted += bool(found)
        notes += len(found)
        kinds.update(w.get("kind", "") for w in found)
    if not n:
        print(f"no AgentHallu trajectories under {root} - nothing to measure. The "
              f"corpus is not in this repository: git clone "
              f"https://github.com/liuxuannan/AgentHallu and pass the clone",
              file=sys.stderr)
        return 2
    print(f"trajectories {n}; with a note {noted} ({noted / n:.1%}); notes {notes} "
          f"({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}); the four echo "
          f"tools declared: {', '.join(sorted(agenthallu.ECHO_TOOLS))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
