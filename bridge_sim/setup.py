"""The setup ceremony that seals an empty vault.

Each member makes one piece of the vault key, locks it under the vault's rule,
publishes the matching public piece and deletes the private piece. The vault's
public key is the sum of the public pieces, so nobody ever holds the whole key.
If at least one member really deletes their piece, the key cannot be rebuilt
without opening the lock.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from .crypto import N, Key, point_add, ser_point
from .lock import BurnStatement, SealedShare, Witness


@dataclass
class SealedVault:
    vault_id: int
    denom: int
    pubkey: bytes
    shares: list
    statement: BurnStatement

    @property
    def storage_bytes(self) -> int:
        return len(self.shares) * SealedShare.NOMINAL_SIZE_BYTES


def seal_vault(statement: BurnStatement, denom: int, members: int = 3,
               dishonest=frozenset(), saboteur=None):
    """Run the ceremony.

    dishonest: member indexes who secretly keep their key piece.
    saboteur:  a member index who locks garbage instead of their real piece.
    Returns (vault, pieces kept by dishonest members).
    """
    shares, kept, total = [], {}, None
    for i in range(members):
        piece = Key.generate()
        locked_value = piece.k if i != saboteur else secrets.randbelow(N - 1) + 1
        shares.append(SealedShare(statement, locked_value, piece.pub))
        if i in dishonest:
            kept[i] = piece.k
        total = point_add(total, piece.point)
    vault = SealedVault(statement.vault_id, denom, ser_point(total), shares, statement)
    return vault, kept


def open_vault(vault: SealedVault, witness: Witness) -> Key:
    """Open every piece with one witness and rebuild the vault's private key."""
    return Key(sum(share.open(witness) for share in vault.shares) % N)
