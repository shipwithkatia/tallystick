"""Claude as a proposer.

Kept deliberately thin: one call, text out. Everything that makes the output
trustworthy happens after this returns, in pipeline._locate, by exact substring
search against the real text. The model here is a pointer generator, not a judge.

Requires `pip install tallystick[propose]` and ANTHROPIC_API_KEY in the environment.
"""

from __future__ import annotations

import os
from typing import Optional


class AnthropicProposer:
    def __init__(self, model: str = "claude-sonnet-4-6", max_tokens: int = 2048,
                 api_key: Optional[str] = None, max_retries: int = 8):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "the Anthropic proposer needs the SDK: pip install 'tallystick[propose]'"
            ) from exc
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        # Long batch jobs (the benchmark) meet rate limits; the SDK's exponential
        # backoff handles them if it is allowed enough attempts.
        self._client = anthropic.Anthropic(api_key=key, max_retries=max_retries)
        self.model = model
        self.max_tokens = max_tokens
        self.name = f"anthropic:{model}"

    def complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in msg.content if getattr(block, "type", "") == "text"
        )
