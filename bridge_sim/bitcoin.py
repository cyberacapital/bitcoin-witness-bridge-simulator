"""A miniature Bitcoin: real proof-of-work headers, Merkle proofs and a UTXO set.

Difficulty is tiny so blocks mine in milliseconds. There are no fees and no
scripts: an output is locked to one public key and spent with one signature.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import cached_property

from .crypto import Key, merkle_proof, merkle_root, sha, verify

COIN = 100_000_000
TARGET = 1 << 244  # about 4,000 hashes per block


class BitcoinError(Exception):
    pass


@dataclass(frozen=True)
class TxOut:
    pubkey: bytes
    amount: int


@dataclass
class Tx:
    inputs: list
    outputs: list
    memo: bytes = b""
    sigs: list = field(default_factory=list)

    @property
    def txid(self) -> bytes:
        return sha(
            "tx",
            [(txid, vout) for txid, vout in self.inputs],
            [(o.pubkey, o.amount) for o in self.outputs],
            self.memo,
        )


def _header_bytes(height: int, prev: bytes, merkle: bytes, nonce: int) -> bytes:
    return height.to_bytes(8, "big") + prev + merkle + nonce.to_bytes(8, "big")


@dataclass(frozen=True)
class Header:
    height: int
    prev: bytes
    merkle: bytes
    nonce: int

    @cached_property
    def hash(self) -> bytes:
        raw = _header_bytes(self.height, self.prev, self.merkle, self.nonce)
        return hashlib.sha256(hashlib.sha256(raw).digest()).digest()

    def pow_ok(self, target: int = TARGET) -> bool:
        return int.from_bytes(self.hash, "big") < target


def mine_header(height: int, prev: bytes, merkle: bytes, target: int = TARGET) -> Header:
    nonce = 0
    while True:
        header = Header(height, prev, merkle, nonce)
        if header.pow_ok(target):
            return header
        nonce += 1


class MiniBitcoin:
    def __init__(self):
        self.headers = [mine_header(0, b"\x00" * 32, sha("genesis"))]
        self.blocks = [[]]
        self.utxos = {}
        self.mempool = []
        self._height_of = {}
        self._faucet_nonce = 0

    @property
    def tip(self) -> Header:
        return self.headers[-1]

    # -- creating transactions

    def faucet(self, pubkey: bytes, amount: int) -> Tx:
        """Test-network faucet: the only way coins come into existence."""
        self._faucet_nonce += 1
        tx = Tx([], [TxOut(pubkey, amount)], memo=b"faucet" + self._faucet_nonce.to_bytes(4, "big"))
        self._apply(tx)
        return tx

    def send(self, key: Key, outputs, memo: bytes = b"") -> Tx:
        """Pay `outputs` from coins owned by `key`, returning change to it."""
        need = sum(o.amount for o in outputs)
        inputs, total = [], 0
        for outpoint, utxo in self.utxos.items():
            if utxo.pubkey == key.pub:
                inputs.append(outpoint)
                total += utxo.amount
                if total >= need:
                    break
        if not inputs or total < need:
            raise BitcoinError("insufficient funds")
        outs = list(outputs)
        if total > need:
            outs.append(TxOut(key.pub, total - need))
        tx = Tx(inputs, outs, memo)
        tx.sigs = [key.sign(tx.txid)] * len(inputs)
        self.broadcast(tx)
        return tx

    def spend(self, outpoint, key: Key, outputs, memo: bytes = b"") -> Tx:
        """Spend one specific output with one specific key."""
        tx = Tx([outpoint], list(outputs), memo)
        tx.sigs = [key.sign(tx.txid)]
        self.broadcast(tx)
        return tx

    # -- validation

    def broadcast(self, tx: Tx) -> None:
        if not tx.inputs:
            raise BitcoinError("only the faucet may create coins")
        if len(tx.sigs) != len(tx.inputs):
            raise BitcoinError("one signature per input is required")
        if len(set(tx.inputs)) != len(tx.inputs):
            raise BitcoinError("duplicate input")
        total_in = 0
        for outpoint, sig in zip(tx.inputs, tx.sigs):
            utxo = self.utxos.get(outpoint)
            if utxo is None:
                raise BitcoinError("input is missing or already spent")
            if not verify(utxo.pubkey, tx.txid, sig):
                raise BitcoinError("invalid signature")
            total_in += utxo.amount
        if any(o.amount <= 0 for o in tx.outputs):
            raise BitcoinError("outputs must be positive")
        if sum(o.amount for o in tx.outputs) > total_in:
            raise BitcoinError("outputs exceed inputs")
        self._apply(tx)

    def _apply(self, tx: Tx) -> None:
        for outpoint in tx.inputs:
            del self.utxos[outpoint]
        for i, out in enumerate(tx.outputs):
            self.utxos[(tx.txid, i)] = out
        self.mempool.append(tx)

    # -- blocks

    def mine(self, n: int = 1) -> Header:
        for _ in range(n):
            txs, self.mempool = self.mempool, []
            header = mine_header(len(self.headers), self.tip.hash, merkle_root([t.txid for t in txs]))
            for tx in txs:
                self._height_of[tx.txid] = header.height
            self.headers.append(header)
            self.blocks.append(txs)
        return self.tip

    def tx_height(self, txid: bytes) -> int:
        height = self._height_of.get(txid)
        if height is None:
            raise BitcoinError("transaction is not in a block")
        return height

    def tx_proof(self, txid: bytes):
        """Return (header, merkle proof) for a confirmed transaction."""
        height = self.tx_height(txid)
        txids = [t.txid for t in self.blocks[height]]
        return self.headers[height], merkle_proof(txids, txids.index(txid))

    def balance(self, pubkey: bytes) -> int:
        return sum(u.amount for u in self.utxos.values() if u.pubkey == pubkey)
