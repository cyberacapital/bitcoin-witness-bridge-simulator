"""A second rule for the same lock: a time lock.

`HeightStatement` accepts a run of Bitcoin headers that grows from the block
known when the lock was made, each with real proof of work, and is at least a
given number of blocks long. A secret locked to it opens for anyone once
Bitcoin has grown that far, with nobody there to release it.

It is here to show that the lock does not care what its rule is: `SealedShare`
takes this statement exactly as it takes the bridge's `BurnStatement`. The lock
is the same simulated one.

Time is counted in blocks of work, not in hours. Whoever can produce that many
blocks alone, in secret, opens the lock without waiting for anyone; a test
pins this down. On real Bitcoin that takes more mining power than the rest of
the network has, and a real rule would also have to follow Bitcoin's changes of
difficulty, which this miniature chain does not have.
"""
from __future__ import annotations

from dataclasses import dataclass

from .bitcoin import TARGET
from .crypto import Key
from .lock import SealedShare, _need


@dataclass(frozen=True)
class HeightStatement:
    """Opens for a chain of `blocks` or more headers with work, grown from `start`."""
    start: bytes          # hash of the Bitcoin block known when the lock was made
    blocks: int
    pow_target: int = TARGET

    def verify(self, headers) -> bool:
        try:
            prev = self.start
            for header in headers:
                _need(header.prev == prev and header.pow_ok(self.pow_target))
                prev = header.hash
            _need(len(headers) >= self.blocks)
            return True
        except Exception:
            return False


def time_lock(start: bytes, blocks: int, secret: Key) -> SealedShare:
    """Lock `secret` so that it opens once Bitcoin has grown `blocks` blocks past `start`."""
    return SealedShare(HeightStatement(start, blocks), secret.k, secret.pub)
