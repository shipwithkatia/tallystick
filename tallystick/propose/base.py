"""The proposer interface, and a fake for tests.

A proposer is anything that turns (system prompt, user prompt) into text. That is
the whole contract. It keeps the pipeline model-agnostic and lets the test suite
drive every branch of the pipeline without a network.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Protocol

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class Proposer(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


def parse_json(text: str) -> Dict[str, Any]:
    """Tolerate a fenced or padded JSON object; fail loudly on anything else.

    Models wrap JSON in ``` fences more often than not. We strip those and take the
    outermost {...}. Anything less well-formed is a proposer bug and is raised, not
    guessed at - a guessed entry would end up in the books.
    """
    cleaned = _FENCE.sub("", text.strip())
    start = cleaned.find("{")
    if start < 0:
        raise ValueError(f"proposer returned no JSON object: {text[:120]!r}")
    # Decode the first complete object and ignore whatever follows it: models
    # sometimes append a sentence, or a second object, after the answer. Taking
    # "first { to last }" instead would break on exactly that.
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"proposer returned malformed JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise ValueError(f"proposer returned {type(obj).__name__}, expected an object")
    return obj


class FakeProposer:
    """Replays canned answers in order. Used by the tests and by
    `tallystick propose --proposer fake --script answers.json`, which exercises the
    whole CLI path with no network.

    Each call pops the next response. A list rather than a prompt-keyed dict keeps
    the fake honest about call order.
    """

    name = "fake"

    def __init__(self, responses: List[Any], pad: Any = None):
        self._responses = [
            r if isinstance(r, str) else json.dumps(r) for r in responses
        ]
        # Optional: what to answer once the list is spent. Off by default so a
        # test that miscounts calls fails loudly; on for tests where the number
        # of credit calls is not the point (coverage claims add calls).
        self._pad = None if pad is None else (pad if isinstance(pad, str) else json.dumps(pad))
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if not self._responses:
            if self._pad is not None:
                return self._pad
            raise RuntimeError("FakeProposer ran out of canned responses")
        return self._responses.pop(0)
