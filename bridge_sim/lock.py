"""The rule baked into every vault, and a SIMULATED witness-encryption lock.

`BurnStatement` is the real logic: the exact checks a proof must pass before a
vault key is released. In a real deployment this logic is compiled into a
zero-knowledge circuit and the key piece is witness-encrypted to it.

`SealedShare` is a stand-in for that ciphertext. It keeps the secret inside a
Python object and hands it out only when the statement accepts a witness. A real
ciphertext is a public file of roughly 8 GB that nobody can open otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass

from .bitcoin import TARGET
from .crypto import merkle_root, merkle_verify, point_mul, ser_point, sha
from .solana import ROOT_TAG, FinalizedRoot, anchor_digest, certificate_ok, decode_handoff, valset_hash


class LockError(Exception):
    pass


class _Reject(Exception):
    pass


def _need(condition: bool) -> None:
    if not condition:
        raise _Reject()


def commitment_for(secret: bytes) -> bytes:
    """The public fingerprint a burner attaches to a burn."""
    return sha("burn-secret", secret)


@dataclass(frozen=True)
class RedemptionRecord:
    """Written into Solana state when tokens are burned for a vault."""
    vault_id: int
    commitment: bytes
    slot: int

    def leaf(self) -> bytes:
        return sha("redeem", self.vault_id, self.commitment, self.slot)


@dataclass
class Witness:
    """Everything a redeemer presents to open a vault."""
    secret: bytes
    record: RedemptionRecord
    record_proof: list
    root: FinalizedRoot
    valsets: list      # validator sets from the checkpoint epoch to the root's epoch
    btc_blocks: list   # (header, transactions) for every block after the checkpoint
    anchor_pos: tuple  # (block index, transaction index) of the root's anchor


@dataclass(frozen=True)
class BurnStatement:
    """A vault opens only for a witness that passes every check below.

    Fixed at sealing time, so it can only refer to things known then: the
    validator set and the Bitcoin block at the moment the vault was sealed.
    """
    vault_id: int
    checkpoint_epoch: int
    checkpoint_valset: bytes
    checkpoint_btc: bytes
    anchor_confirmations: int = 6
    pow_target: int = TARGET

    def verify(self, w: Witness) -> bool:
        try:
            self._verify(w)
            return True
        except Exception:
            return False

    def _verify(self, w: Witness) -> None:
        # 1. The burn record is for this vault, and the redeemer knows its secret.
        _need(w.record.vault_id == self.vault_id)
        _need(commitment_for(w.secret) == w.record.commitment)
        # 2. The record is part of the Solana state the root commits to.
        _need(merkle_verify(w.record.leaf(), w.record_proof, w.root.state_root))
        # 3. Start from the validator set fixed when the vault was sealed.
        _need(len(w.valsets) >= 1 and valset_hash(w.valsets[0]) == self.checkpoint_valset)
        # 4. Replay Bitcoin from the checkpoint. Bitcoin's order decides which
        #    validator set is current: the first valid hand-off for an epoch wins,
        #    and a root only counts if it was anchored while its epoch was current.
        prev = self.checkpoint_btc
        epoch = self.checkpoint_epoch
        idx = 0
        anchored_at = None
        for b, (header, txs) in enumerate(w.btc_blocks):
            _need(header.prev == prev and header.pow_ok(self.pow_target))
            _need(merkle_root([tx.txid for tx in txs]) == header.merkle)
            prev = header.hash
            if anchored_at is not None:
                continue
            for t, tx in enumerate(txs):
                if (b, t) == tuple(w.anchor_pos):
                    _need(tx.memo == ROOT_TAG + anchor_digest(w.root))
                    _need(w.root.epoch == epoch)
                    _need(certificate_ok(w.valsets[idx], w.root.msg, w.root.sigs))
                    anchored_at = b
                    break
                handoff = decode_handoff(tx.memo)
                if (
                    handoff is not None
                    and handoff.epoch == epoch
                    and certificate_ok(w.valsets[idx], handoff.msg, handoff.sigs)
                ):
                    # A valid hand-off cannot be skipped: the witness must follow it.
                    _need(idx + 1 < len(w.valsets))
                    _need(valset_hash(w.valsets[idx + 1]) == handoff.next_hash)
                    epoch += 1
                    idx += 1
        # 5. The anchor exists and is buried under enough proof of work.
        _need(anchored_at is not None)
        _need(len(w.btc_blocks) - anchored_at >= self.anchor_confirmations)


class SealedShare:
    """SIMULATED witness-encryption ciphertext holding one piece of a vault key."""

    NOMINAL_SIZE_BYTES = int(7.88 * 2**30)

    def __init__(self, statement: BurnStatement, secret: int, public_point: bytes):
        self.statement = statement
        self.public_point = public_point
        self.__secret = secret

    def audit(self) -> bool:
        """Stand-in for the setup-honesty proof: does the hidden piece match
        the public key piece? In reality this is a zero-knowledge proof."""
        return ser_point(point_mul(self.__secret)) == self.public_point

    def open(self, witness: Witness) -> int:
        if not self.statement.verify(witness):
            raise LockError("witness rejected: the vault stays sealed")
        return self.__secret
