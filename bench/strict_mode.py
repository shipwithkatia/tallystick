"""How much of AgentHallu `--require-declared-tools` would fail.

    python bench/strict_mode.py DIR

DIR holds the AgentHallu trajectories, which are not in this repository:
git clone https://github.com/liuxuannan/AgentHallu. Each trajectory is rendered
as a chat log (bench/openai_roundtrip.py) and read with the corpus's four
echo-returning tools declared, as `--tool-returns-model-text` would; strict mode
fails a run when any tool result comes from a tool declared nothing about. The
count is the one `check-trace --require-declared-tools` applies
(`cli._undeclared_tool_results`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from openai_roundtrip import render  # noqa: E402
from tallystick.adapters import agenthallu, openai_chat  # noqa: E402
from tallystick.cli import _undeclared_tool_results  # noqa: E402


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    root = Path(argv[0])
    n = failed = 0
    for p in sorted(root.rglob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not (isinstance(obj, dict) and "history" in obj):
            continue
        n += 1
        trace = openai_chat.to_trace(render(obj), name=p.name,
                                     model_text_tools=agenthallu.ECHO_TOOLS)
        undeclared = _undeclared_tool_results(trace)
        failed += bool(undeclared and undeclared[0])
    if not n:
        # The same division by zero as echo_coverage.py (review 16, 5.2).
        print(f"no AgentHallu trajectories under {root} - nothing to measure. The "
              f"corpus is not in this repository: git clone "
              f"https://github.com/liuxuannan/AgentHallu and pass the clone",
              file=sys.stderr)
        return 2
    print(f"trajectories {n}; strict mode fails {failed} ({failed / n:.1%}) "
          f"with the four echo tools declared: {', '.join(sorted(agenthallu.ECHO_TOOLS))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
