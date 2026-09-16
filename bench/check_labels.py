"""Every RAGTruth label's span addresses the text the label carries.

    python bench/check_labels.py [RAGTRUTH_DIR]

The test suite runs this check on the six items in bench/sample; this runs it
on the whole dataset. RAGTruth is not in this repository: bench/build.py clones
https://github.com/ParticleMedia/RAGTruth into bench/work/RAGTruth (the default
here), or pass a clone. Exit 1 if any label does not match.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))

import build  # noqa: E402


def main(argv) -> int:
    root = Path(argv[0]) if argv else ROOT / "bench" / "work" / "RAGTruth"
    responses, _sources = build.load_ragtruth(root)
    labels = bad = 0
    for r in responses:
        for lab in r["labels"]:
            labels += 1
            if r["response"][lab["start"]:lab["end"]] != lab["text"]:
                bad += 1
    print(f"responses {len(responses)}, labels {labels}, span does not match its text: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
