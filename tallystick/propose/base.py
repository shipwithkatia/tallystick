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
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"proposer returned no JSON object: {text[:120]!r}")
    return json.loads(cleaned[start:end + 1])


class FakeProposer:
    """Replays canned answers in order. Used by the tests and by
    `tallystick propose --proposer fake --script answers.json`, which exercises the
    whole CLI path with no network.

    Each call pops the next response. A list rather than a prompt-keyed dict keeps
    the fake honest about call order.
    """

    name = "fake"

    def __init__(self, responses: List[Any]):
        self._responses = [
            r if isinstance(r, str) else json.dumps(r) for r in responses
        ]

    def complete(self, system: str, user: str) -> str:
        if not self._responses:
            raise RuntimeError("FakeProposer ran out of canned responses")
        return self._responses.pop(0)
