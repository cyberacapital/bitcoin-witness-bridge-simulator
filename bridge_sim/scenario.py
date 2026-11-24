"""A ready-made world (both chains, the bridge, a relayer) for tests and the demo."""
from __future__ import annotations

import secrets

from .bitcoin import COIN, MiniBitcoin, TxOut
from .bridge import BridgeProgram
from .client import anchor_handoff, anchor_root, build_witness, relay_headers
from .crypto import Key
from .launchpad import Launchpad
from .lock import BurnStatement, commitment_for
from .setup import seal_vault
from .solana import MiniSolana, Validator, valset_hash


class World:
    DENOM = COIN // 10  # every vault in the default world holds 0.1 BTC

    def __init__(self, validators: int = 4, members: int = 3):
        self.btc = MiniBitcoin()
        self.validators = [Validator(Key.generate(), 100) for _ in range(validators)]
        self.sol = MiniSolana(self.validators)
        self.bridge = BridgeProgram(self.sol, self.btc.headers[0])
        self.sol.program = self.bridge
        self.launchpad = Launchpad(self.bridge)
        self.members = members
        self.vaults = {}
        self._next_vault = 1
        self.relayer = self.funded_user(COIN)

    # -- plumbing

    def relay(self) -> None:
        relay_headers(self.btc, self.bridge)

    def confirm(self, blocks: int = 6) -> None:
        self.btc.mine(blocks)
        self.relay()

    def funded_user(self, sats: int) -> Key:
        key = Key.generate()
        self.btc.faucet(key.pub, sats)
        self.confirm(1)
        return key

    # -- setup

    def seal(self, denom: int = None, **ceremony):
        statement = BurnStatement(
            vault_id=self._next_vault,
            checkpoint_epoch=self.sol.epoch,
            checkpoint_valset=valset_hash(self.sol.valset),
            checkpoint_btc=self.btc.tip.hash,
        )
        self._next_vault += 1
        vault, kept = seal_vault(statement, denom or self.DENOM, members=self.members, **ceremony)
        self.vaults[vault.vault_id] = vault
        return vault, kept

    def seal_and_register(self, denom: int = None, **ceremony):
        vault, kept = self.seal(denom, **ceremony)
        self.bridge.register_vault(vault)
        return vault, kept

    # -- deposit and mint

    def deposit(self, user: Key, vault, amount: int = None):
        return self.btc.send(user, [TxOut(vault.pubkey, amount or vault.denom)], memo=user.pub)

    def mint(self, vault, tx) -> None:
        header, proof = self.btc.tx_proof(tx.txid)
        self.bridge.mint(vault.vault_id, tx, header.hash, proof)

    def deposit_and_mint(self, user: Key, vault):
        tx = self.deposit(user, vault)
        self.confirm()
        self.mint(vault, tx)
        return tx

    # -- burn and redeem

    def burn(self, user: Key, denom: int = None, secret: bytes = None):
        secret = secret or secrets.token_bytes(32)
        record = self.bridge.burn(user, denom or self.DENOM, commitment_for(secret))
        return record, secret

    def finalize_and_anchor(self, blocks: int = 6):
        root = self.sol.finalize()
        tx = anchor_root(self.btc, self.relayer, root)
        self.confirm(blocks)
        return root, tx

    def witness(self, vault, record, secret, root, anchor_tx):
        return build_witness(vault.statement, self.sol, self.btc, record, secret, root, anchor_tx)

    # -- validator rotation

    def rotate(self):
        """Start a new epoch and anchor the hand-off on Bitcoin. Returns the old validators."""
        old = self.validators
        new = [Validator(Key.generate(), 100) for _ in old]
        handoff = self.sol.rotate(new)
        anchor_handoff(self.btc, self.relayer, handoff)
        self.confirm(1)
        self.validators = new
        return old
