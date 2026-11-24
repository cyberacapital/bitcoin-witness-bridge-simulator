"""The bridge program that lives on Solana.

It follows Bitcoin's block headers, registers sealed vaults, mints tokens
against confirmed deposits, moves tokens, and records burns.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .bitcoin import TARGET, Header
from .crypto import Key, deser_point, merkle_verify, point_add, ser_point, sha, verify
from .lock import RedemptionRecord
from .setup import SealedVault


class BridgeError(Exception):
    pass


class InvariantBroken(Exception):
    pass


@dataclass
class VaultEntry:
    vault_id: int
    pubkey: bytes
    denom: int
    status: str = "EMPTY"   # EMPTY -> FUNDED -> REDEEMED
    deposit: tuple = None


class BridgeProgram:
    CONFIRMATIONS = 6

    def __init__(self, solana, btc_genesis: Header, pow_target: int = TARGET):
        self.solana = solana
        self.pow_target = pow_target
        self.headers = {btc_genesis.hash: btc_genesis}
        self.tip = btc_genesis
        self.vaults = {}
        self.queues = {}
        self.balances = {}
        self.supply = 0
        self.records = []
        self._used_deposits = set()
        self._used_commitments = set()
        self._programs = {}

    # -- Bitcoin light client

    def submit_header(self, header: Header) -> None:
        if header.hash in self.headers:
            return
        parent = self.headers.get(header.prev)
        if parent is None:
            raise BridgeError("header does not connect to a known block")
        if header.height != parent.height + 1:
            raise BridgeError("header has the wrong height")
        if not header.pow_ok(self.pow_target):
            raise BridgeError("header lacks proof of work")
        self.headers[header.hash] = header
        if header.height > self.tip.height:
            self.tip = header

    def _confirmations(self, header: Header) -> int:
        cursor = self.tip
        while cursor.height > header.height:
            cursor = self.headers[cursor.prev]
        if cursor.hash != header.hash:
            return 0
        return self.tip.height - header.height + 1

    # -- vault registry

    def register_vault(self, vault: SealedVault) -> None:
        if vault.vault_id in self.vaults:
            raise BridgeError("vault id is already registered")
        if vault.denom <= 0 or not vault.shares:
            raise BridgeError("malformed vault")
        total = None
        for share in vault.shares:
            if share.statement != vault.statement or share.statement.vault_id != vault.vault_id:
                raise BridgeError("a key piece is locked to a different rule")
            if not share.audit():
                raise BridgeError("a sealed piece does not match its public key piece")
            total = point_add(total, deser_point(share.public_point))
        if ser_point(total) != vault.pubkey:
            raise BridgeError("key pieces do not add up to the vault key")
        self.vaults[vault.vault_id] = VaultEntry(vault.vault_id, vault.pubkey, vault.denom)

    # -- mint

    def mint(self, vault_id: int, tx, header_hash: bytes, proof) -> None:
        """Anyone may submit a deposit proof; tokens go to the address in the memo."""
        entry = self.vaults.get(vault_id)
        if entry is None:
            raise BridgeError("unknown vault")
        if entry.status != "EMPTY":
            raise BridgeError("vault is not empty")
        header = self.headers.get(header_hash)
        if header is None:
            raise BridgeError("unknown Bitcoin block")
        if self._confirmations(header) < self.CONFIRMATIONS:
            raise BridgeError("deposit needs more confirmations")
        if not merkle_verify(tx.txid, proof, header.merkle):
            raise BridgeError("transaction is not in that block")
        matches = [i for i, out in enumerate(tx.outputs) if out.pubkey == entry.pubkey]
        if len(matches) != 1 or tx.outputs[matches[0]].amount != entry.denom:
            raise BridgeError("deposit must pay the vault exactly its denomination")
        outpoint = (tx.txid, matches[0])
        if outpoint in self._used_deposits:
            raise BridgeError("deposit was already used")
        if len(tx.memo) != 64:
            raise BridgeError("deposit memo must name the Solana recipient")
        self._used_deposits.add(outpoint)
        entry.status = "FUNDED"
        entry.deposit = outpoint
        self.queues.setdefault(entry.denom, deque()).append(vault_id)
        self.balances[tx.memo] = self.balances.get(tx.memo, 0) + entry.denom
        self.supply += entry.denom

    # -- token movement

    @staticmethod
    def _auth(sender: Key, msg: bytes) -> None:
        if not verify(sender.pub, msg, sender.sign(msg)):
            raise BridgeError("bad signature")

    def transfer(self, sender: Key, to: bytes, amount: int) -> None:
        self._auth(sender, sha("transfer", to, amount))
        if amount <= 0 or self.balances.get(sender.pub, 0) < amount:
            raise BridgeError("insufficient balance")
        self.balances[sender.pub] -= amount
        self.balances[to] = self.balances.get(to, 0) + amount

    # -- other Solana programs holding bridged Bitcoin (for example a trading pool)

    def register_program(self, program) -> None:
        """Give another program an account that only that program can spend from."""
        if program.address in self._programs or program.address in self.balances:
            raise BridgeError("program address is already in use")
        self._programs[program.address] = program

    def transfer_from_program(self, program, to: bytes, amount: int) -> None:
        """A registered program moves tokens out of its own account."""
        if self._programs.get(program.address) is not program:
            raise BridgeError("caller does not own that account")
        if amount <= 0 or self.balances.get(program.address, 0) < amount:
            raise BridgeError("insufficient balance")
        self.balances[program.address] -= amount
        self.balances[to] = self.balances.get(to, 0) + amount

    # -- burn

    def burn(self, sender: Key, denom: int, commitment: bytes) -> RedemptionRecord:
        """Destroy `denom` tokens and assign the oldest funded vault of that size."""
        self._auth(sender, sha("burn", denom, commitment))
        if self.balances.get(sender.pub, 0) < denom:
            raise BridgeError("insufficient balance")
        queue = self.queues.get(denom)
        if not queue:
            raise BridgeError("no funded vault of that size")
        if commitment in self._used_commitments:
            raise BridgeError("commitment was already used")
        self._used_commitments.add(commitment)
        vault_id = queue.popleft()
        self.vaults[vault_id].status = "REDEEMED"
        self.balances[sender.pub] -= denom
        self.supply -= denom
        record = RedemptionRecord(vault_id, commitment, self.solana.slot)
        self.records.append(record)
        return record

    def record_leaves(self):
        return [record.leaf() for record in self.records]

    # -- the rule that must always hold

    def check_invariant(self, btc) -> bool:
        funded = [v for v in self.vaults.values() if v.status == "FUNDED"]
        if self.supply != sum(self.balances.values()):
            raise InvariantBroken("balances do not add up to supply")
        if self.supply != sum(v.denom for v in funded):
            raise InvariantBroken("token supply differs from sealed Bitcoin")
        for v in funded:
            utxo = btc.utxos.get(v.deposit)
            if utxo is None or utxo.pubkey != v.pubkey or utxo.amount != v.denom:
                raise InvariantBroken(f"vault {v.vault_id} is no longer backed")
        return True
