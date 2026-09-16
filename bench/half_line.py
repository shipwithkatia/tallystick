"""What the half line costs on this project's posted traces.

    python bench/half_line.py

`audit` exits 1 with `answer_mostly_unclaimed` when more than half of the
answer's letters and digits stand under no claim. A trace whose ONLY reason for
exit 1 is that one would exit 0 without the rule: those are the traces the rule
moved. Counted over every posted trace of the benchmark runs
(bench/work*/**/posted/*.json - RAGTruth and AgentHallu, the current runs and
earlier ones) and examples/*.json.

The benchmark files are written by bench/run.py and bench/agenthallu.py, which
call a model, and are not in this repository; without them only examples/ is
counted, and the script says so.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tallystick.cli import ANSWER_MOSTLY_UNCLAIMED, main as tallystick  # noqa: E402


def main() -> int:
    posted = sorted(ROOT.glob("bench/work*/**/posted/*.json"))
    files = posted + sorted((ROOT / "examples").glob("*.json"))
    report = Path(tempfile.mkdtemp()) / "report.json"
    codes: Counter = Counter()
    any_reason = only_reason = 0
    for path in files:
        report.unlink(missing_ok=True)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = tallystick(["audit", str(path), "--quiet", "--json", str(report)])
        codes[code] += 1
        if not report.exists():
            continue
        reasons = json.loads(report.read_text(encoding="utf-8"))["gate"]["reasons"]
        any_reason += ANSWER_MOSTLY_UNCLAIMED in reasons
        only_reason += reasons == [ANSWER_MOSTLY_UNCLAIMED]
    print(f"posted traces {len(files)} ({len(posted)} from bench/work*, "
          f"{len(files) - len(posted)} from examples/)")
    print(f"exit codes {dict(sorted(codes.items()))}")
    print(f"{ANSWER_MOSTLY_UNCLAIMED} among the reasons: {any_reason}; the only reason "
          f"(moved from exit 0 to exit 1 by the half line): {only_reason}")
    if not posted:
        print("the benchmark's posted traces are not in this repository (bench/run.py and "
              "bench/agenthallu.py write them, with a model); only examples/ was counted",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
