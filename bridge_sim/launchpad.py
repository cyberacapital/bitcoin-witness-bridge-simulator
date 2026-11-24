"""Launching new coins on Solana, priced directly against bridged Bitcoin.

Each coin gets one trading pool holding bridged Bitcoin on one side and the coin
on the other. The price is simply the ratio between the two, so it is quoted in
Bitcoin. Buying adds Bitcoin and removes coins; selling does the reverse.

The pool's Bitcoin never leaves the bridge's books: it sits in an account only
this program can spend from, and is still backed one-for-one by sealed vaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .bridge import BridgeProgram, InvariantBroken
from .crypto import Key, sha

FEE_BPS = 30  # 0.30% of every trade stays in the pool


class LaunchError(Exception):
    pass


@dataclass
class Pool:
    symbol: str
    creator: bytes
    btc: int      # bridged Bitcoin in the pool, in satoshis
    coins: int    # coins in the pool
    locked: bool  # True: the starting liquidity can never be withdrawn


class Launchpad:
    def __init__(self, bridge: BridgeProgram):
        self.bridge = bridge
        self.address = sha("program", "launchpad") + sha("program-account", "launchpad")
        bridge.register_program(self)
        self.pools = {}
        self.supplies = {}
        self.coin_balances = {}

    # -- launching

    def launch(self, creator: Key, symbol: str, supply: int, btc_liquidity: int,
               creator_share: int = 0, lock_liquidity: bool = True) -> Pool:
        """Create a coin and its pool.

        The creator pays `btc_liquidity` in bridged Bitcoin. `creator_share`
        coins go to the creator; the rest of the supply goes into the pool.
        """
        if symbol in self.pools:
            raise LaunchError("that symbol is taken")
        if supply <= 0 or btc_liquidity <= 0 or not 0 <= creator_share < supply:
            raise LaunchError("malformed launch")
        self.bridge.transfer(creator, self.address, btc_liquidity)
        pool = Pool(symbol, creator.pub, btc_liquidity, supply - creator_share, lock_liquidity)
        self.pools[symbol] = pool
        self.supplies[symbol] = supply
        self.coin_balances[symbol] = {creator.pub: creator_share} if creator_share else {}
        return pool

    # -- reading

    def _pool(self, symbol: str) -> Pool:
        pool = self.pools.get(symbol)
        if pool is None:
            raise LaunchError("unknown coin")
        return pool

    def price(self, symbol: str) -> Fraction:
        """Satoshis of bridged Bitcoin per coin."""
        pool = self._pool(symbol)
        if pool.coins == 0:
            raise LaunchError("the pool is empty")
        return Fraction(pool.btc, pool.coins)

    def balance(self, symbol: str, pubkey: bytes) -> int:
        return self.coin_balances.get(symbol, {}).get(pubkey, 0)

    def quote_buy(self, symbol: str, btc_in: int) -> int:
        pool = self._pool(symbol)
        net = btc_in - btc_in * FEE_BPS // 10_000
        if btc_in <= 0 or net <= 0:
            return 0
        return pool.coins * net // (pool.btc + net)

    def quote_sell(self, symbol: str, coins_in: int) -> int:
        pool = self._pool(symbol)
        net = coins_in - coins_in * FEE_BPS // 10_000
        if coins_in <= 0 or net <= 0:
            return 0
        return pool.btc * net // (pool.coins + net)

    # -- trading

    def buy(self, trader: Key, symbol: str, btc_in: int, min_coins_out: int = 0) -> int:
        """Pay bridged Bitcoin, receive coins. Fails if fewer than `min_coins_out`."""
        pool = self._pool(symbol)
        out = self.quote_buy(symbol, btc_in)
        if out <= 0 or out < min_coins_out:
            raise LaunchError("order would fill below the minimum, or the pool is empty")
        self.bridge.transfer(trader, self.address, btc_in)
        pool.btc += btc_in
        pool.coins -= out
        book = self.coin_balances[symbol]
        book[trader.pub] = book.get(trader.pub, 0) + out
        return out

    def sell(self, trader: Key, symbol: str, coins_in: int, min_btc_out: int = 0) -> int:
        """Pay coins, receive bridged Bitcoin. Fails if less than `min_btc_out`."""
        pool = self._pool(symbol)
        self.bridge._auth(trader, sha("sell", symbol, coins_in))
        if coins_in <= 0 or self.balance(symbol, trader.pub) < coins_in:
            raise LaunchError("insufficient coins")
        out = self.quote_sell(symbol, coins_in)
        if out <= 0 or out < min_btc_out:
            raise LaunchError("order would fill below the minimum, or the pool is empty")
        self.coin_balances[symbol][trader.pub] -= coins_in
        pool.coins += coins_in
        pool.btc -= out
        self.bridge.transfer_from_program(self, trader.pub, out)
        return out

    # -- liquidity

    def withdraw_liquidity(self, creator: Key, symbol: str):
        """Only possible when the pool was launched unlocked. Empties the pool."""
        pool = self._pool(symbol)
        self.bridge._auth(creator, sha("withdraw", symbol))
        if creator.pub != pool.creator:
            raise LaunchError("only the creator can withdraw liquidity")
        if pool.locked:
            raise LaunchError("this pool's liquidity is locked for good")
        btc, coins = pool.btc, pool.coins
        pool.btc = pool.coins = 0
        if btc:
            self.bridge.transfer_from_program(self, creator.pub, btc)
        book = self.coin_balances[symbol]
        book[creator.pub] = book.get(creator.pub, 0) + coins
        return btc, coins

    # -- rules that must always hold

    def check_invariant(self) -> bool:
        held = self.bridge.balances.get(self.address, 0)
        if sum(pool.btc for pool in self.pools.values()) > held:
            raise InvariantBroken("pools claim more Bitcoin than the program holds")
        for symbol, pool in self.pools.items():
            if sum(self.coin_balances[symbol].values()) + pool.coins != self.supplies[symbol]:
                raise InvariantBroken(f"{symbol} supply does not add up")
        return True
