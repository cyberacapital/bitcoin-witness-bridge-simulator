"""Off-chain helpers: relaying headers, posting anchors, assembling a witness."""
from __future__ import annotations

from .bitcoin import MiniBitcoin, Tx
from .crypto import Key
from .lock import BurnStatement, RedemptionRecord, Witness
from .solana import ROOT_TAG, FinalizedRoot, Handoff, MiniSolana, anchor_digest, encode_handoff


def relay_headers(btc: MiniBitcoin, bridge) -> None:
    """Feed any Bitcoin headers the bridge has not seen yet. Anyone can do this."""
    for header in btc.headers:
        if header.hash not in bridge.headers:
            bridge.submit_header(header)


def anchor_root(btc: MiniBitcoin, payer: Key, root: FinalizedRoot) -> Tx:
    """Write a fingerprint of a finalised Solana root into Bitcoin."""
    return btc.send(payer, [], memo=ROOT_TAG + anchor_digest(root))


def anchor_handoff(btc: MiniBitcoin, payer: Key, handoff: Handoff) -> Tx:
    """Write a validator hand-off into Bitcoin, where its order becomes final."""
    return btc.send(payer, [], memo=encode_handoff(handoff))


def bitcoin_evidence(btc: MiniBitcoin, checkpoint_hash: bytes, anchor_tx: Tx):
    """Every block after the checkpoint, plus where the anchor sits in them."""
    start = next(i for i, h in enumerate(btc.headers) if h.hash == checkpoint_hash)
    blocks = [(btc.headers[i], list(btc.blocks[i])) for i in range(start + 1, len(btc.headers))]
    height = btc.tx_height(anchor_tx.txid)
    tx_index = [t.txid for t in btc.blocks[height]].index(anchor_tx.txid)
    return blocks, (height - (start + 1), tx_index)


def build_witness(statement: BurnStatement, solana: MiniSolana, btc: MiniBitcoin,
                  record: RedemptionRecord, secret: bytes, root: FinalizedRoot,
                  anchor_tx: Tx) -> Witness:
    blocks, pos = bitcoin_evidence(btc, statement.checkpoint_btc, anchor_tx)
    valsets = solana.valsets[statement.checkpoint_epoch:root.epoch + 1]
    proof = solana.record_proof(root, record.leaf())
    return Witness(secret, record, proof, root, valsets, blocks, pos)
