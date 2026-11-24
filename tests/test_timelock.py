"""Tests for the time lock: a second rule for the same simulated lock.

It shows that the lock takes any rule, not only the bridge's. The test named
KNOWN_LIMIT is an attack that succeeds on purpose.
"""
import unittest

from bridge_sim import (BurnStatement, Header, HeightStatement, Key, LockError, MiniBitcoin,
                        SealedShare, mine_header, sha, time_lock)


class TimeLock(unittest.TestCase):
    BLOCKS = 5

    def setUp(self):
        self.btc = MiniBitcoin()
        self.btc.mine(2)
        self.start = self.btc.tip                       # the block known when the lock is made
        self.secret = Key.generate()
        self.lock = time_lock(self.start.hash, self.BLOCKS, self.secret)

    def since_start(self):
        return self.btc.headers[self.start.height + 1:]

    def test_it_stays_shut_until_bitcoin_has_grown_far_enough(self):
        self.assertTrue(self.lock.audit())
        for _ in range(self.BLOCKS):
            with self.assertRaises(LockError):
                self.lock.open(self.since_start())
            self.btc.mine()
        self.assertEqual(self.lock.open(self.since_start()), self.secret.k)
        self.btc.mine(3)                                # it stays open afterwards
        self.assertEqual(self.lock.open(self.since_start()), self.secret.k)

    def test_headers_without_work_or_grown_from_elsewhere_are_refused(self):
        self.btc.mine(self.BLOCKS)
        good = self.since_start()
        workless, prev = [], self.start.hash
        for i in range(self.BLOCKS):
            nonce = next(n for n in range(10) if not Header(self.start.height + 1 + i, prev, sha("x"), n).pow_ok())
            workless.append(Header(self.start.height + 1 + i, prev, sha("x"), nonce))
            prev = workless[-1].hash
        attempts = {
            "no work": workless,
            "grown from another block": self.btc.headers[1:1 + self.BLOCKS],
            "a block missing": good[:2] + good[3:] + good[-1:],
            "out of order": good[1:] + good[:1],
            "too few": good[:self.BLOCKS - 1],
            "nothing": [],
            "not headers at all": "let me in",
        }
        for name, attempt in attempts.items():
            with self.subTest(name):
                with self.assertRaises(LockError):
                    self.lock.open(attempt)
        self.assertEqual(self.lock.open(good), self.secret.k)

    def test_KNOWN_LIMIT_blocks_mined_in_secret_open_it_early(self):
        """Time is counted in work. Whoever can make the blocks alone does not have to wait
        for the network: here that takes milliseconds, on Bitcoin more power than everyone else."""
        private, prev = [], self.start.hash
        for i in range(self.BLOCKS):
            private.append(mine_header(self.start.height + 1 + i, prev, sha("mined in secret", i)))
            prev = private[-1].hash
        self.assertEqual(self.btc.tip, self.start)       # the public chain has not moved
        self.assertEqual(self.lock.open(private), self.secret.k)

    def test_the_lock_is_the_one_the_bridge_uses(self):
        self.assertIsInstance(self.lock, SealedShare)
        self.assertIsInstance(self.lock.statement, HeightStatement)
        self.assertNotIsInstance(self.lock.statement, BurnStatement)
        self.assertEqual(SealedShare.NOMINAL_SIZE_BYTES, int(7.88 * 2**30))


if __name__ == "__main__":
    unittest.main()
