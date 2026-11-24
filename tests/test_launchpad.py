"""Tests for launching coins priced against bridged Bitcoin.

Tests named WARNING are things that work as coded but hurt buyers. They are here
so nobody mistakes a trust-minimised bridge for a safe coin.
"""
import random
import unittest
from fractions import Fraction

from bridge_sim import COIN, BridgeError, Key, LaunchError, TxOut, World, open_vault

SUPPLY = 1_000_000


def bridged_user(world, vaults=2):
    """A user who has moved `vaults` x 0.1 BTC across the bridge."""
    user = world.funded_user(COIN)
    for _ in range(vaults):
        vault, _ = world.seal_and_register()
        world.deposit_and_mint(user, vault)
    return user


class LaunchingCoins(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.pad = self.w.launchpad
        self.alice = bridged_user(self.w)
        self.bob = bridged_user(self.w)
        self.denom = self.w.DENOM

    def check(self):
        self.w.bridge.check_invariant(self.w.btc)
        self.pad.check_invariant()

    def test_bridge_launch_trade_and_exit_back_to_native_bitcoin(self):
        w, pad, alice, bob = self.w, self.pad, self.alice, self.bob
        pad.launch(alice, "MOON", SUPPLY, btc_liquidity=self.denom)
        start = pad.price("MOON")
        self.assertEqual(start, Fraction(self.denom, SUPPLY))  # 10 satoshis per coin

        spent = self.denom // 2
        coins = pad.buy(bob, "MOON", spent)
        self.assertGreater(pad.price("MOON"), start)
        back = pad.sell(bob, "MOON", coins)
        self.assertLess(back, spent)                 # fees
        self.assertGreater(back, spent * 99 // 100)  # but only fees
        self.check()

        # Bob leaves: burn one vault's worth of tokens and take native Bitcoin.
        before = w.btc.balance(bob.pub)
        record, secret = w.burn(bob)
        root, anchor = w.finalize_and_anchor()
        vault = w.vaults[record.vault_id]
        key = open_vault(vault, w.witness(vault, record, secret, root, anchor))
        w.btc.spend(w.bridge.vaults[vault.vault_id].deposit, key, [TxOut(bob.pub, vault.denom)])
        self.assertEqual(w.btc.balance(bob.pub), before + vault.denom)
        self.check()

    def test_locked_liquidity_can_never_be_withdrawn(self):
        pad = self.pad
        pad.launch(self.alice, "MOON", SUPPLY, self.denom)
        pad.buy(self.bob, "MOON", self.denom)
        with self.assertRaises(LaunchError):
            pad.withdraw_liquidity(self.alice, "MOON")
        self.assertEqual(pad.pools["MOON"].btc, 2 * self.denom)
        self.check()

    def test_WARNING_unlocked_liquidity_can_be_pulled_by_the_creator(self):
        w, pad, alice, bob = self.w, self.pad, self.alice, self.bob
        pad.launch(alice, "RUG", SUPPLY, self.denom, lock_liquidity=False)
        coins = pad.buy(bob, "RUG", self.denom)
        with self.assertRaises(LaunchError):
            pad.withdraw_liquidity(bob, "RUG")  # only the creator can
        before = w.bridge.balances[alice.pub]
        btc, _ = pad.withdraw_liquidity(alice, "RUG")
        self.assertEqual(btc, 2 * self.denom)  # her own stake plus everything Bob paid
        self.assertEqual(w.bridge.balances[alice.pub], before + 2 * self.denom)
        with self.assertRaises(LaunchError):
            pad.sell(bob, "RUG", coins)  # nothing left to sell into
        self.check()  # the bridge itself is unharmed: every token is still backed

    def test_WARNING_a_creator_who_keeps_coins_can_dump_them_despite_a_locked_pool(self):
        pad, alice, bob = self.pad, self.alice, self.bob
        pad.launch(alice, "DUMP", SUPPLY, self.denom, creator_share=SUPPLY // 2)
        start = pad.price("DUMP")
        pad.buy(bob, "DUMP", self.denom)
        self.assertGreater(pad.price("DUMP"), start)
        taken = pad.sell(alice, "DUMP", SUPPLY // 2)
        self.assertGreater(taken, self.denom)        # she takes out more than she put in
        self.assertLess(pad.price("DUMP"), start)    # and the price ends below launch
        self.check()

    def test_bad_orders_change_nothing(self):
        w, pad, alice, bob = self.w, self.pad, self.alice, self.bob
        mallory = Key.generate()
        pad.launch(alice, "MOON", SUPPLY, self.denom)
        quote = pad.quote_buy("MOON", 1_000_000)
        bob_before = w.bridge.balances[bob.pub]
        with self.assertRaises(LaunchError):
            pad.buy(bob, "MOON", 1_000_000, min_coins_out=quote + 1)  # price moved against him
        with self.assertRaises(BridgeError):
            pad.buy(bob, "MOON", 10 * COIN)  # more bridged Bitcoin than Bob holds
        with self.assertRaises(LaunchError):
            pad.sell(bob, "MOON", 1)  # coins Bob does not hold
        with self.assertRaises(LaunchError):
            pad.launch(bob, "MOON", SUPPLY, self.denom)  # symbol taken
        with self.assertRaises(BridgeError):
            pad.launch(mallory, "FREE", SUPPLY, self.denom)  # no bridged Bitcoin to back it
        self.assertEqual((pad.pools["MOON"].btc, pad.pools["MOON"].coins), (self.denom, SUPPLY))
        self.assertEqual(w.bridge.balances[bob.pub], bob_before)
        self.assertNotIn("FREE", pad.pools)
        self.check()

    def test_sell_respects_the_minimum_the_seller_asked_for(self):
        pad, bob = self.pad, self.bob
        pad.launch(self.alice, "MOON", SUPPLY, self.denom)
        coins = pad.buy(bob, "MOON", self.denom // 2)
        quote = pad.quote_sell("MOON", coins)
        with self.assertRaises(LaunchError):
            pad.sell(bob, "MOON", coins, min_btc_out=quote + 1)
        self.assertEqual(pad.balance("MOON", bob.pub), coins)
        self.assertEqual(pad.sell(bob, "MOON", coins, min_btc_out=quote), quote)

    def test_every_trade_pays_a_fee_of_three_tenths_of_a_percent(self):
        """Worked by hand for a pool of 0.1 BTC (10,000,000 sats) and 1,000,000 coins."""
        pad = self.pad
        pad.launch(self.alice, "MOON", SUPPLY, self.denom)
        self.assertEqual(pad.quote_buy("MOON", 1_000_000), 90_661)    # 90,909 with no fee
        self.assertEqual(pad.quote_sell("MOON", 100_000), 906_610)    # 909,090 with no fee
        self.assertEqual(pad.buy(self.bob, "MOON", 1_000_000), 90_661)
        self.assertEqual(pad.pools["MOON"].btc, 11_000_000)           # the fee stays in the pool

    def test_only_the_launchpad_can_move_pool_bitcoin(self):
        w, pad = self.w, self.pad
        pad.launch(self.alice, "MOON", SUPPLY, self.denom)

        class Impostor:
            address = pad.address

        with self.assertRaises(BridgeError):
            w.bridge.transfer_from_program(Impostor(), Key.generate().pub, 1)
        with self.assertRaises(BridgeError):
            w.bridge.register_program(Impostor())
        with self.assertRaises(BridgeError):
            w.bridge.transfer_from_program(pad, Key.generate().pub, self.denom + 1)  # more than it holds
        self.assertEqual(w.bridge.balances[pad.address], self.denom)
        self.check()

    def test_pool_bitcoin_stays_fully_backed_under_random_trading(self):
        w, pad = self.w, self.pad
        rng = random.Random(11)
        users = [self.alice, self.bob, bridged_user(w)]
        pool = pad.launch(self.alice, "MOON", SUPPLY, self.denom)
        k = pool.btc * pool.coins
        trades = 0
        for _ in range(60):
            trader = rng.choice(users)
            held = pad.balance("MOON", trader.pub)
            btc = w.bridge.balances.get(trader.pub, 0)
            try:
                if held and rng.random() < 0.5:
                    pad.sell(trader, "MOON", rng.randint(1, held))
                elif btc >= 2000:
                    pad.buy(trader, "MOON", rng.randint(1000, btc // 2))
                else:
                    continue
            except LaunchError:
                continue
            trades += 1
            self.assertGreaterEqual(pool.btc * pool.coins, k)  # the pool never loses on a trade
            k = pool.btc * pool.coins
            self.check()
        self.assertGreater(trades, 30)


if __name__ == "__main__":
    unittest.main()
