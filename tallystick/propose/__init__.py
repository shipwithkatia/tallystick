"""The model side. Everything in this package may call a model; nothing outside it may.

The split is physical, not conventional:

  tallystick/*.py          the verdict path. Deterministic, offline, zero deps.
  tallystick/propose/      proposers. Segment text into claims, propose credits.

A proposer's output is written to a file - a *posted* trace - and the verdict is
computed from that file. Two runs of the proposer may differ. Two audits of the
same posted trace never do. `tests/test_no_model_imports.py` enforces that the
verdict path never imports from here.

    from tallystick.propose import post_run, AnthropicProposer
    posted = post_run(raw_run, AnthropicProposer())
    balance = audit(posted)
"""

from .base import FakeProposer, Proposer
from .pipeline import ProposalLog, post_run

__all__ = ["post_run", "ProposalLog", "Proposer", "FakeProposer", "AnthropicProposer"]


def __getattr__(name):
    # Lazy so that importing tallystick.propose never requires the anthropic SDK
    # unless the Anthropic proposer is actually used.
    if name == "AnthropicProposer":
        from .anthropic_proposer import AnthropicProposer
        return AnthropicProposer
    raise AttributeError(name)
