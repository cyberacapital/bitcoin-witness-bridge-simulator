"""Tests for the bridge logic. Run with:  python -m unittest -v

Most tests are attacks that must fail. Tests named ASSUMPTION or KNOWN_LIMIT are
attacks that succeed on purpose: they pin down exactly what the design trusts.
"""
import random
import unittest

from bridge_sim import (
    COIN,
    N,
    ROOT_TAG,
    BitcoinError,
    BridgeError,
    FinalizedRoot,
    Handoff,
    Header,
    InvariantBroken,
    Key,
    LockError,
    RedemptionRecord,
    Tx,
    TxOut,
    Validator,
    Witness,
    World,
    anchor_digest,
    anchor_handoff,
    anchor_root,
    bitcoin_evidence,
    commitment_for,
    merkle_root,
    mine_header,
    open_vault,
    sha,
    valset_hash,
)


def forge_root(signers, epoch, slot, leaves):
    state_root = merkle_root(leaves)
    msg = sha("root", epoch, slot, state_root)
    return FinalizedRoot(epoch, slot, state_root, {v.key.pub: v.key.sign(msg) for v in signers})


def forge_handoff(signers, epoch, next_infos):
    next_hash = valset_hash(next_infos)
    msg = sha("handoff", epoch, next_hash)
    return Handoff(epoch, next_hash, {v.key.pub: v.key.sign(msg) for v in signers})


def private_fork(btc, checkpoint_hash, first_block_txs, length):
    """Blocks an attacker mines in secret on top of the checkpoint."""
    height = next(h.height for h in btc.headers if h.hash == checkpoint_hash)
    blocks, prev = [], checkpoint_hash
    for i in range(length):
        txs = first_block_txs if i == 0 else []
        header = mine_header(height + 1 + i, prev, merkle_root([t.txid for t in txs]))
        blocks.append((header, txs))
        prev = header.hash
    return blocks


def funded_vault(world, **ceremony):
    """A world state where Alice has deposited into one vault and holds the tokens."""
    alice = world.funded_user(COIN)
    vault, kept = world.seal_and_register(**ceremony)
    world.deposit_and_mint(alice, vault)
    return alice, vault, kept


class HappyPath(unittest.TestCase):
    def test_deposit_mint_transfer_burn_and_redeem(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        bob = Key.generate()
        self.assertEqual(w.bridge.balances[alice.pub], vault.denom)
        w.bridge.check_invariant(w.btc)

        w.bridge.transfer(alice, bob.pub, vault.denom)
        record, secret = w.burn(bob)
        root, anchor = w.finalize_and_anchor()
        key = open_vault(vault, w.witness(vault, record, secret, root, anchor))

        self.assertEqual(key.pub, vault.pubkey)
        w.btc.spend(w.bridge.vaults[vault.vault_id].deposit, key, [TxOut(bob.pub, vault.denom)])
        self.assertEqual(w.btc.balance(bob.pub), vault.denom)
        self.assertEqual(w.bridge.supply, 0)
        w.bridge.check_invariant(w.btc)

    def test_burns_still_redeem_after_validators_rotate_twice(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        w.rotate()
        w.rotate()
        record, secret = w.burn(alice)
        root, anchor = w.finalize_and_anchor()
        self.assertEqual(root.epoch, 2)
        key = open_vault(vault, w.witness(vault, record, secret, root, anchor))
        self.assertEqual(key.pub, vault.pubkey)

    def test_redemption_waits_for_bitcoin_confirmations(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        record, secret = w.burn(alice)
        root, anchor = w.finalize_and_anchor(blocks=3)
        with self.assertRaises(LockError):
            open_vault(vault, w.witness(vault, record, secret, root, anchor))
        w.confirm(3)
        open_vault(vault, w.witness(vault, record, secret, root, anchor))


class TokenAccounting(unittest.TestCase):
    def test_one_deposit_mints_once_and_one_vault_redeems_once(self):
        w = World()
        alice = w.funded_user(COIN)
        vault_a, _ = w.seal_and_register()
        vault_b, _ = w.seal_and_register()
        tx_a = w.deposit_and_mint(alice, vault_a)
        w.deposit_and_mint(alice, vault_b)
        with self.assertRaises(BridgeError):
            w.mint(vault_a, tx_a)

        first, secret = w.burn(alice)
        with self.assertRaises(BridgeError):
            w.burn(alice, secret=secret)  # a burn secret cannot be reused
        second, _ = w.burn(alice)
        self.assertNotEqual(first.vault_id, second.vault_id)
        with self.assertRaises(BridgeError):
            w.burn(alice)  # nothing left to burn
        w.bridge.check_invariant(w.btc)

    def test_a_second_deposit_into_a_funded_vault_mints_nothing(self):
        """Otherwise two burns would be pointed at one vault, and the first takes all."""
        w = World()
        alice = w.funded_user(COIN)
        bob = w.funded_user(COIN)
        vault, _ = w.seal_and_register()
        w.deposit_and_mint(alice, vault)
        second = w.deposit(bob, vault)
        w.confirm()
        with self.assertRaises(BridgeError):
            w.mint(vault, second)
        self.assertEqual(w.bridge.supply, vault.denom)
        self.assertEqual(w.bridge.balances.get(bob.pub, 0), 0)

    def test_you_cannot_burn_or_send_tokens_you_do_not_hold(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        mallory = Key.generate()
        with self.assertRaises(BridgeError):
            w.burn(mallory)  # a funded vault exists, but Mallory holds no tokens
        with self.assertRaises(BridgeError):
            w.bridge.transfer(mallory, mallory.pub, vault.denom)
        with self.assertRaises(BridgeError):
            w.bridge.transfer(alice, mallory.pub, vault.denom + 1)
        self.assertEqual(w.bridge.vaults[vault.vault_id].status, "FUNDED")
        w.bridge.check_invariant(w.btc)

    def test_tokens_go_to_the_depositor_whoever_submits_the_proof(self):
        w = World()
        alice = w.funded_user(COIN)
        mallory = Key.generate()
        vault, _ = w.seal_and_register()
        tx = w.deposit(alice, vault)
        w.confirm()
        w.mint(vault, tx)  # mint has no caller identity: Mallory could have sent this
        self.assertEqual(w.bridge.balances.get(alice.pub), vault.denom)
        self.assertEqual(w.bridge.balances.get(mallory.pub, 0), 0)

    def test_supply_always_matches_sealed_bitcoin_under_random_activity(self):
        rng = random.Random(7)
        w = World()
        users = [w.funded_user(COIN) for _ in range(3)]
        empty = [w.seal_and_register()[0] for _ in range(8)]
        done = {"deposit": 0, "transfer": 0, "burn": 0}
        for _ in range(30):
            holders = [u for u in users if w.bridge.balances.get(u.pub, 0) >= w.DENOM]
            options = (["deposit"] if empty else []) + (["transfer", "transfer", "burn"] if holders else [])
            if not options:
                break
            action = rng.choice(options)
            if action == "deposit":
                w.deposit_and_mint(rng.choice(users), empty.pop())
            elif action == "transfer":
                w.bridge.transfer(rng.choice(holders), rng.choice(users).pub, w.DENOM)
            else:
                w.burn(rng.choice(holders))
            done[action] += 1
            w.bridge.check_invariant(w.btc)
        self.assertTrue(all(count >= 3 for count in done.values()), done)


class FakeDeposits(unittest.TestCase):
    def test_fake_or_wrong_deposits_do_not_mint(self):
        w = World()
        alice = w.funded_user(COIN)
        mallory = Key.generate()
        vault, _ = w.seal_and_register()

        tx = w.deposit(alice, vault)
        with self.assertRaises(BitcoinError):
            w.btc.tx_proof(tx.txid)  # not in a block yet, so no proof exists

        w.confirm(2)
        with self.assertRaises(BridgeError):
            w.mint(vault, tx)  # too few confirmations

        other = w.btc.send(alice, [TxOut(mallory.pub, 1000)])
        w.confirm(6)
        header, proof = w.btc.tx_proof(other.txid)
        with self.assertRaises(BridgeError):
            w.bridge.mint(vault.vault_id, tx, header.hash, proof)  # proof is for another tx

        vault2, _ = w.seal_and_register()
        short = w.deposit(alice, vault2, amount=vault2.denom // 2)
        w.confirm()
        with self.assertRaises(BridgeError):
            w.mint(vault2, short)  # wrong amount

        self.assertEqual(w.bridge.supply, 0)
        w.mint(vault, tx)  # the honest deposit still works
        self.assertEqual(w.bridge.supply, vault.denom)

    def test_bridge_rejects_bitcoin_headers_without_work_or_ancestry(self):
        w = World()
        tip = w.btc.tip
        nonce = 0
        while Header(tip.height + 1, tip.hash, sha("x"), nonce).pow_ok():
            nonce += 1
        with self.assertRaises(BridgeError):
            w.bridge.submit_header(Header(tip.height + 1, tip.hash, sha("x"), nonce))
        orphan = mine_header(tip.height + 1, sha("nowhere"), sha("x"))
        with self.assertRaises(BridgeError):
            w.bridge.submit_header(orphan)


class RacingTheBurner(unittest.TestCase):
    def test_observer_without_the_secret_cannot_open_the_vault(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        record, secret = w.burn(alice)
        root, anchor = w.finalize_and_anchor()
        # Everything public is identical. Only the secret differs.
        with self.assertRaises(LockError):
            open_vault(vault, w.witness(vault, record, b"mallory guesses", root, anchor))
        open_vault(vault, w.witness(vault, record, secret, root, anchor))

    def test_a_valid_proof_for_one_vault_does_not_open_another(self):
        w = World()
        alice = w.funded_user(COIN)
        vault_a, _ = w.seal_and_register()
        vault_b, _ = w.seal_and_register()
        w.deposit_and_mint(alice, vault_a)
        w.deposit_and_mint(alice, vault_b)
        record, secret = w.burn(alice)
        self.assertEqual(record.vault_id, vault_a.vault_id)
        root, anchor = w.finalize_and_anchor()
        witness = w.witness(vault_a, record, secret, root, anchor)
        with self.assertRaises(LockError):
            open_vault(vault_b, witness)


class SetupCeremony(unittest.TestCase):
    def test_one_honest_member_is_enough(self):
        w = World()
        alice, vault, kept = funded_vault(w, dishonest={0, 1})
        partial = Key(sum(kept.values()) % N)
        self.assertNotEqual(partial.pub, vault.pubkey)
        with self.assertRaises(BitcoinError):
            w.btc.spend(w.bridge.vaults[vault.vault_id].deposit, partial,
                        [TxOut(Key.generate().pub, vault.denom)])
        w.bridge.check_invariant(w.btc)

    def test_ASSUMPTION_if_every_member_cheats_the_vault_is_theirs(self):
        w = World()
        alice, vault, kept = funded_vault(w, dishonest={0, 1, 2})
        full = Key(sum(kept.values()) % N)
        self.assertEqual(full.pub, vault.pubkey)
        w.btc.spend(w.bridge.vaults[vault.vault_id].deposit, full,
                    [TxOut(Key.generate().pub, vault.denom)])
        with self.assertRaises(InvariantBroken):
            w.bridge.check_invariant(w.btc)

    def test_sabotaged_key_piece_is_rejected_before_anyone_deposits(self):
        w = World()
        vault, _ = w.seal(saboteur=1)
        with self.assertRaises(BridgeError):
            w.bridge.register_vault(vault)


class ForgedSolanaHistory(unittest.TestCase):
    """Mallory wants a vault that is still funded and whose tokens Alice still holds."""

    SECRET = b"mallory"

    def fake_record(self, vault):
        return RedemptionRecord(vault.vault_id, commitment_for(self.SECRET), 99)

    def test_a_burn_that_solana_never_recorded_is_refused(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        root, anchor = w.finalize_and_anchor()  # an honest root with no burns in it
        blocks, pos = bitcoin_evidence(w.btc, vault.statement.checkpoint_btc, anchor)
        witness = Witness(self.SECRET, self.fake_record(vault), [], root,
                          [w.sol.valsets[0]], blocks, pos)
        with self.assertRaises(LockError):
            open_vault(vault, witness)

    def test_validators_mallory_invented_herself_are_refused(self):
        """The vault only trusts the validator set fixed on the day it was sealed."""
        w = World()
        alice, vault, _ = funded_vault(w)
        fake = self.fake_record(vault)
        evil = [Validator(Key.generate(), 100) for _ in range(4)]
        root = forge_root(evil, 0, 99, [fake.leaf()])
        anchor = anchor_root(w.btc, w.relayer, root)
        w.confirm()
        blocks, pos = bitcoin_evidence(w.btc, vault.statement.checkpoint_btc, anchor)
        witness = Witness(self.SECRET, fake, [], root, [[v.info for v in evil]], blocks, pos)
        with self.assertRaises(LockError):
            open_vault(vault, witness)

    def test_a_minority_of_validators_cannot_forge_a_burn(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        fake = self.fake_record(vault)
        root = forge_root(w.validators[:2], 0, 99, [fake.leaf()])  # half the stake
        anchor = anchor_root(w.btc, w.relayer, root)
        w.confirm()
        blocks, pos = bitcoin_evidence(w.btc, vault.statement.checkpoint_btc, anchor)
        witness = Witness(self.SECRET, fake, [], root, [w.sol.valsets[0]], blocks, pos)
        with self.assertRaises(LockError):
            open_vault(vault, witness)

    def test_ASSUMPTION_a_corrupt_supermajority_of_current_validators_can_forge(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        fake = self.fake_record(vault)
        root = forge_root(w.validators[:3], 0, 99, [fake.leaf()])  # three quarters
        anchor = anchor_root(w.btc, w.relayer, root)
        w.confirm()
        blocks, pos = bitcoin_evidence(w.btc, vault.statement.checkpoint_btc, anchor)
        witness = Witness(self.SECRET, fake, [], root, [w.sol.valsets[0]], blocks, pos)
        self.assertEqual(open_vault(vault, witness).pub, vault.pubkey)

    def test_leaked_old_validator_keys_fail_once_the_handoff_is_on_bitcoin(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        old = w.rotate()  # hand-off anchored on Bitcoin; afterwards every old key leaks
        fake = self.fake_record(vault)
        checkpoint = vault.statement.checkpoint_btc

        # Attempt 1: sign a root as the retired set and pretend no hand-off happened.
        root = forge_root(old, 0, 99, [fake.leaf()])
        anchor = anchor_root(w.btc, w.relayer, root)
        w.confirm()
        blocks, pos = bitcoin_evidence(w.btc, checkpoint, anchor)
        ignore_handoff = Witness(self.SECRET, fake, [], root, [w.sol.valsets[0]], blocks, pos)
        self.assertFalse(vault.statement.verify(ignore_handoff))

        # Attempt 2: admit the hand-off. The forged root now belongs to a retired epoch.
        admit = Witness(self.SECRET, fake, [], root, w.sol.valsets[0:2], blocks, pos)
        self.assertFalse(vault.statement.verify(admit))

        # Attempt 3: forge a rival hand-off to Mallory's own validators. It lands
        # on Bitcoin after the honest one, so the honest one wins.
        evil = [Validator(Key.generate(), 100) for _ in range(4)]
        evil_infos = [v.info for v in evil]
        anchor_handoff(w.btc, w.relayer, forge_handoff(old, 0, evil_infos))
        w.confirm(1)
        evil_root = forge_root(evil, 1, 100, [fake.leaf()])
        anchor2 = anchor_root(w.btc, w.relayer, evil_root)
        w.confirm()
        blocks, pos = bitcoin_evidence(w.btc, checkpoint, anchor2)
        rival = Witness(self.SECRET, fake, [], evil_root, [w.sol.valsets[0], evil_infos], blocks, pos)
        with self.assertRaises(LockError):
            open_vault(vault, rival)

    def test_leaked_keys_cannot_backdate_a_root_to_before_the_handoff(self):
        """Mallory never anchors her root; she points at an old, unrelated transaction."""
        w = World()
        alice, vault, _ = funded_vault(w)
        deposit_txid = w.bridge.vaults[vault.vault_id].deposit[0]
        old = w.rotate()
        fake = self.fake_record(vault)
        root = forge_root(old, 0, 99, [fake.leaf()])
        height = w.btc.tx_height(deposit_txid)
        deposit_tx = next(t for t in w.btc.blocks[height] if t.txid == deposit_txid)
        blocks, pos = bitcoin_evidence(w.btc, vault.statement.checkpoint_btc, deposit_tx)
        witness = Witness(self.SECRET, fake, [], root, [w.sol.valsets[0]], blocks, pos)
        with self.assertRaises(LockError):
            open_vault(vault, witness)

    def test_hiding_the_handoff_inside_a_real_block_is_detected(self):
        """Mallory shows the real headers but leaves the hand-off out of its block."""
        w = World()
        alice, vault, _ = funded_vault(w)
        old = w.rotate()
        fake = self.fake_record(vault)
        root = forge_root(old, 0, 99, [fake.leaf()])
        anchor = anchor_root(w.btc, w.relayer, root)
        w.confirm()
        blocks, pos = bitcoin_evidence(w.btc, vault.statement.checkpoint_btc, anchor)
        censored = [(h, [t for t in txs if not t.memo.startswith(b"HANDOFF")]) for h, txs in blocks]
        self.assertNotEqual([len(t) for _, t in blocks], [len(t) for _, t in censored])
        witness = Witness(self.SECRET, fake, [], root, [w.sol.valsets[0]], censored, pos)
        with self.assertRaises(LockError):
            open_vault(vault, witness)

    def test_a_private_fork_without_real_work_or_ancestry_is_refused(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        old = w.rotate()
        fake = self.fake_record(vault)
        root = forge_root(old, 0, 99, [fake.leaf()])
        anchor = Tx([], [], memo=ROOT_TAG + anchor_digest(root))
        checkpoint = vault.statement.checkpoint_btc

        # Blocks that link up correctly but were never mined.
        lazy, prev = [], checkpoint
        for i in range(6):
            txs = [anchor] if i == 0 else []
            nonce = 0
            while Header(100 + i, prev, merkle_root([t.txid for t in txs]), nonce).pow_ok():
                nonce += 1
            header = Header(100 + i, prev, merkle_root([t.txid for t in txs]), nonce)
            lazy.append((header, txs))
            prev = header.hash
        no_work = Witness(self.SECRET, fake, [], root, [w.sol.valsets[0]], lazy, (0, 0))
        with self.assertRaises(LockError):
            open_vault(vault, no_work)

        # Properly mined blocks that do not grow from the vault's checkpoint.
        elsewhere = private_fork(w.btc, w.btc.headers[0].hash, [anchor], length=6)
        wrong_root = Witness(self.SECRET, fake, [], root, [w.sol.valsets[0]], elsewhere, (0, 0))
        with self.assertRaises(LockError):
            open_vault(vault, wrong_root)

    def _private_mining_attack(self, w, vault, old):
        fake = self.fake_record(vault)
        root = forge_root(old, 0, 99, [fake.leaf()])
        anchor = Tx([], [], memo=ROOT_TAG + anchor_digest(root))
        blocks = private_fork(w.btc, vault.statement.checkpoint_btc, [anchor], length=6)
        return Witness(self.SECRET, fake, [], root, [w.sol.valsets[0]], blocks, (0, 0))

    def test_KNOWN_LIMIT_leaked_keys_plus_private_mining_open_an_idle_vault(self):
        """The lock cannot tell the real Bitcoin chain from one mined in secret.

        With a retired supermajority's keys AND six privately mined blocks that
        leave out the hand-off, Mallory opens a vault that is still funded. The
        cost of those blocks is therefore the ceiling on what the bridge protects.
        """
        w = World()
        alice, vault, _ = funded_vault(w)
        old = w.rotate()
        stolen = open_vault(vault, self._private_mining_attack(w, vault, old))
        w.btc.spend(w.bridge.vaults[vault.vault_id].deposit, stolen,
                    [TxOut(Key.generate().pub, vault.denom)])
        with self.assertRaises(InvariantBroken):
            w.bridge.check_invariant(w.btc)

    def test_a_vault_already_redeemed_is_safe_from_that_attack(self):
        w = World()
        alice, vault, _ = funded_vault(w)
        record, secret = w.burn(alice)
        root, anchor = w.finalize_and_anchor()
        key = open_vault(vault, w.witness(vault, record, secret, root, anchor))
        outpoint = w.bridge.vaults[vault.vault_id].deposit
        w.btc.spend(outpoint, key, [TxOut(alice.pub, vault.denom)])

        old = w.rotate()
        late_key = open_vault(vault, self._private_mining_attack(w, vault, old))
        with self.assertRaises(BitcoinError):
            w.btc.spend(outpoint, late_key, [TxOut(Key.generate().pub, vault.denom)])


if __name__ == "__main__":
    unittest.main()
