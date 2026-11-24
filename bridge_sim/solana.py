"""A miniature Solana: stake-weighted validators that sign finalised state roots.

Each epoch has one validator set. At the end of an epoch the outgoing set signs
a hand-off to the next set. The bridge posts that hand-off on Bitcoin so that
Bitcoin's block order, not anyone's say-so, decides which set is current.

Real Solana uses ed25519; this model reuses the simulator's Schnorr signatures.
"""
from __future__ import annotations

from dataclasses import dataclass

from .crypto import Key, Signature, merkle_proof, merkle_root, sha, verify

HANDOFF_TAG = b"HANDOFF"
ROOT_TAG = b"ROOT"


@dataclass(frozen=True)
class ValidatorInfo:
    pubkey: bytes
    stake: int


@dataclass
class Validator:
    key: Key
    stake: int

    @property
    def info(self) -> ValidatorInfo:
        return ValidatorInfo(self.key.pub, self.stake)


def valset_hash(valset) -> bytes:
    return sha("valset", [(v.pubkey, v.stake) for v in valset])


def certificate_ok(valset, msg: bytes, sigs: dict) -> bool:
    """True if validators holding more than two thirds of stake signed `msg`."""
    total = sum(v.stake for v in valset)
    signed = 0
    seen = set()
    for v in valset:
        if v.pubkey in seen:
            return False
        seen.add(v.pubkey)
        sig = sigs.get(v.pubkey)
        if sig is not None and verify(v.pubkey, msg, sig):
            signed += v.stake
    return total > 0 and signed * 3 > total * 2


@dataclass(frozen=True)
class Handoff:
    epoch: int
    next_hash: bytes
    sigs: dict

    @property
    def msg(self) -> bytes:
        return sha("handoff", self.epoch, self.next_hash)


@dataclass(frozen=True)
class FinalizedRoot:
    epoch: int
    slot: int
    state_root: bytes
    sigs: dict

    @property
    def msg(self) -> bytes:
        return sha("root", self.epoch, self.slot, self.state_root)


def anchor_digest(root: FinalizedRoot) -> bytes:
    return sha("anchor", root.epoch, root.slot, root.state_root)


def encode_handoff(handoff: Handoff) -> bytes:
    """Serialise a hand-off so the whole certificate sits in a Bitcoin memo.

    On real Bitcoin this would be a short zero-knowledge proof, not raw signatures.
    """
    out = HANDOFF_TAG + handoff.epoch.to_bytes(8, "big") + handoff.next_hash
    out += len(handoff.sigs).to_bytes(2, "big")
    for pub, sig in sorted(handoff.sigs.items()):
        out += pub + sig.r_point + sig.s.to_bytes(32, "big")
    return out


def decode_handoff(memo: bytes):
    """Parse a hand-off memo. Returns None for anything malformed."""
    if not memo.startswith(HANDOFF_TAG):
        return None
    body = memo[len(HANDOFF_TAG):]
    if len(body) < 42:
        return None
    epoch = int.from_bytes(body[:8], "big")
    next_hash = body[8:40]
    count = int.from_bytes(body[40:42], "big")
    rest = body[42:]
    if len(rest) != count * 160:
        return None
    sigs = {}
    for i in range(count):
        chunk = rest[i * 160:(i + 1) * 160]
        sigs[chunk[:64]] = Signature(chunk[64:128], int.from_bytes(chunk[128:], "big"))
    return Handoff(epoch, next_hash, sigs)


class MiniSolana:
    def __init__(self, validators):
        self.validators = list(validators)
        self.epoch = 0
        self.slot = 0
        self.valsets = [[v.info for v in self.validators]]
        self.handoffs = []
        self.roots = []
        self.program = None
        self._snapshots = {}

    @property
    def valset(self):
        return self.valsets[-1]

    def _sign_all(self, msg: bytes) -> dict:
        return {v.key.pub: v.key.sign(msg) for v in self.validators}

    def rotate(self, new_validators) -> Handoff:
        """End the epoch: the outgoing validators sign over to the new set."""
        nxt = [v.info for v in new_validators]
        next_hash = valset_hash(nxt)
        msg = sha("handoff", self.epoch, next_hash)
        handoff = Handoff(self.epoch, next_hash, self._sign_all(msg))
        self.handoffs.append(handoff)
        self.valsets.append(nxt)
        self.validators = list(new_validators)
        self.epoch += 1
        return handoff

    def finalize(self) -> FinalizedRoot:
        """Finalise the current bridge state under the current validator set."""
        self.slot += 1
        leaves = self.program.record_leaves() if self.program else []
        state_root = merkle_root(leaves)
        msg = sha("root", self.epoch, self.slot, state_root)
        root = FinalizedRoot(self.epoch, self.slot, state_root, self._sign_all(msg))
        self.roots.append(root)
        self._snapshots[self.slot] = leaves
        return root

    def record_proof(self, root: FinalizedRoot, leaf: bytes):
        leaves = self._snapshots[root.slot]
        return merkle_proof(leaves, leaves.index(leaf))
