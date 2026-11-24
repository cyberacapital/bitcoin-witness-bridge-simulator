"""Tests for the Praxis lock at toy parameters. Run with:  python -m unittest -v

What these can show: the code computes what the Lean definitions say, and a
solution opens a ciphertext. What they cannot show: security. Toy parameters
have none, and the scheme itself is new and unreviewed.
"""
import importlib.util
import random
import secrets
import unittest

from praxis_lock import (BN254Groups, Coins, ExponentGroups, Gate, ParamError, Params, QAP,
                         and_equals, and_gate, bit_equals, c_x, decrypt, encrypt, exponents,
                         find_small_multiple, forms_of, honest_parts, is_witness, pad, r_int,
                         sample_coins, valid_coins, xor_equals, xor_gate)
from praxis_lock import reference
from praxis_lock.field import Q, inv, pcoeff, peval, pmul, padd, interpolate
from praxis_lock.kummer import cells, leg_exp, weight, weight_products, z_of
from praxis_lock.scheme import SCALAR_COINS

HAVE_PAIRINGS = importlib.util.find_spec("py_ecc") is not None

PARAM_SETS = {
    "default": dict(),
    "smallest": dict(N=3, gates=1, U=2, n=4, d=2, K1=1, g=3),
    "one K2": dict(N=3, gates=2, U=2, n=4, d=2, K1=2, g=5),
    "wide gates": dict(N=6, gates=2, U=4, n=16, d=4, K1=4, g=5),
    "one row": dict(N=5, gates=4, U=2, n=8, d=4, K1=2, g=9),
    "cube roots": dict(N=7, gates=1, U=8, n=24, d=4, K1=3, g=7),
}


def puzzle(P):
    """b1 xor b2 = 1, b2 and b3 = 1, b3 xor b4 = 0. One solution: 0, 1, 1, 1."""
    return pad(P, [xor_equals(1, 2, 1), and_equals(2, 3, 1), xor_equals(3, 4, 0)]), [1, 0, 1, 1, 1]


def random_statement(P, rng):
    """A random statement together with a solution of it."""
    x = [1] + [rng.randrange(2) for _ in range(P.N)]

    def form():
        return {rng.randrange(P.N + 1): rng.randrange(1, Q) for _ in range(rng.randrange(1, 4))}

    gates = []
    for _ in range(P.gates_max):
        products = rng.randrange(P.products_max + 1)
        left = tuple(form() for _ in range(products))
        right = tuple(form() for _ in range(products))
        total = sum(sum(c * x[i] for i, c in l.items()) * sum(c * x[i] for i, c in r.items())
                    for l, r in zip(left, right)) % Q
        linear = {i: c for i, c in form().items() if i != 0}
        linear[0] = (total - sum(c * x[i] for i, c in linear.items())) % Q
        gates.append(Gate(left, right, linear))
    assert is_witness(P, gates, x)
    return tuple(gates), x


class Parameters(unittest.TestCase):
    def test_every_toy_set_satisfies_the_structural_conditions(self):
        for name, kw in PARAM_SETS.items():
            with self.subTest(name):
                Params.toy(**kw)

    def test_broken_parameters_are_rejected(self):
        with self.assertRaises(ParamError):
            Params.toy(g=8)                       # g must be odd
        with self.assertRaises(ParamError):
            Params.toy(n=8, K1=3)                 # K1 K2 must equal m
        with self.assertRaises(ParamError):
            Params.toy(n=10, d=2, K1=5)           # n must divide q - 1
        with self.assertRaises(ParamError):
            Params.toy(c0=4)                      # X^n - 4 is reducible

    def test_the_papers_parameters_pass_the_same_checks(self):
        Params.paper().validate()

    def test_element_counts_reproduce_the_published_ciphertext_size(self):
        P = Params.paper()
        # `L1` and `L2` in the Lean constants count the generators as well.
        self.assertEqual(P.count_g1(), 151468814 - 1)
        self.assertEqual(P.count_g2(), 56828073 - 1)
        gib = P.ciphertext_bytes() / 2**30
        self.assertTrue(7.8 < gib < 8.0, gib)     # the paper reports 7.88 GiB


class Statements(unittest.TestCase):
    def test_gate_helpers_mean_what_they_say(self):
        P = Params.toy(N=3, gates=1, U=2, n=4, d=2, K1=1, g=3)
        for a in (0, 1):
            for b in (0, 1):
                for c in (0, 1):
                    x = [1, a, b, c]
                    self.assertEqual(is_witness(P, [and_gate(1, 2, 3)], x), (a & b) == c)
                    self.assertEqual(is_witness(P, [xor_gate(1, 2, 3)], x), (a ^ b) == c)
                    self.assertEqual(is_witness(P, [and_equals(1, 2, c)], x), (a & b) == c)
                    self.assertEqual(is_witness(P, [xor_equals(1, 2, c)], x), (a ^ b) == c)
                    self.assertEqual(is_witness(P, [bit_equals(1, c)], x), a == c)

    def test_solutions_must_be_bits_with_a_leading_one(self):
        P = Params.toy()
        gates, x = puzzle(P)
        self.assertTrue(is_witness(P, gates, x))
        self.assertFalse(is_witness(P, gates, [2] + x[1:]))
        self.assertFalse(is_witness(P, gates, [1, 0, 1, 1, 2]))
        self.assertFalse(is_witness(P, gates, [1, 1, 1, 1, 1]))


class QuadraticProgram(unittest.TestCase):
    def test_honest_quotient_satisfies_both_identities(self):
        rng = random.Random(1)
        for name, kw in PARAM_SETS.items():
            with self.subTest(name):
                P = Params.toy(**kw)
                gates, x = random_statement(P, rng)
                qap = QAP(P, gates)
                hk, ho, R = qap.honest_quotient(x)
                self.assertLess(len(R), P.U * P.gates_max + 1)
                self.assertFalse(any(pcoeff(R, k) for k in range(0, P.U * P.gates_max, P.U)))
                self.assertLessEqual(len(ho), P.E1 * P.Mq)
                self.assertLessEqual(len(hk), P.E1 * P.Mq)
                for _ in range(3):
                    t = rng.randrange(Q)
                    A, B, l_rho, z, z0 = qap.eval_at(t)
                    ab = sum(x[i] * A[i] for i in range(P.N + 1)) * sum(
                        x[i] * B[i] for i in range(P.N + 1)) % Q
                    self.assertEqual((ab - l_rho) % Q,
                                     (peval(hk, t) * z * (t - qap.rho) + z0 * peval(R, t)) % Q)
                    self.assertEqual(ab, (peval(ho, t) * z + z0 * peval(R, t)) % Q)

    def test_no_quotient_exists_for_a_wrong_solution(self):
        rng = random.Random(2)
        P = Params.toy()
        gates, x = puzzle(P)
        qap = QAP(P, gates)
        for bad in ([1, 1, 1, 1, 1], [1, 0, 0, 1, 1], [1, 0, 1, 1, 0], [1, 0, 1, 1, 2],
                    [1, 0, 1, 1, Q - 1]):
            self.assertIsNone(qap.honest_quotient(bad), bad)
        gates, x = random_statement(P, rng)
        flipped = list(x)
        flipped[2] ^= 1
        if not is_witness(P, gates, flipped):
            self.assertIsNone(QAP(P, gates).honest_quotient(flipped))

    def test_public_points_are_distinct_and_cosets_do_not_collide(self):
        for name, kw in PARAM_SETS.items():
            P = Params.toy(**kw)
            qap = QAP(P, pad(P, []))
            self.assertEqual(len(set(qap.xs)), P.D)
            self.assertEqual(len({pow(d, P.U, Q) for d in qap.delta}), P.gates_max)
            self.assertEqual(pow(qap.mu, P.U, Q), 1)


class ProofAlgebra(unittest.TestCase):
    """The identities the Lean correctness proof establishes (`decref_correct`)."""

    def setup(self, name, seed):
        rng = random.Random(seed)
        P = Params.toy(**PARAM_SETS[name])
        gates, x = random_statement(P, rng)
        G = ExponentGroups()
        qap = QAP(P, gates)
        coins = sample_coins(P, qap, rng)
        ct = encrypt(G, P, gates, bytes(P.message_bytes), coins=coins, seed=rng.randbytes(32))
        return P, gates, x, G, qap, coins, ct

    def test_lambda_is_key_plus_a_small_multiple_of_psi(self):
        for name in PARAM_SETS:
            with self.subTest(name):
                P, gates, x, G, qap, c, ct = self.setup(name, 3)
                wk, sectors = honest_parts(G, P, qap, ct, x)
                v = forms_of(P, ct.seed)
                _, _, _, kappa = exponents(P, qap, v, c)
                prod_u, prod_v = weight_products(P, z_of(P, v, x))
                s = c.s

                def gamma(cell):
                    k1, leg = cell
                    pi_at = s["pi"] * pow(s["u"], 2 * k1, Q) * (1 + s["eps"] * pow(P.beta, 2 * k1, Q))
                    b = {"E": s["bE"], "O": s["bO"], "V": s["bV"]}[leg[0]]
                    return pi_at * b * pow(s["u"], leg_exp(P, leg), Q) % Q

                xy = sum(a * b for a, b in zip(x, c.y)) % Q
                psi = sum(weight(P, prod_u, prod_v, cell) * gamma(cell) for cell in cells(P)) * xy % Q
                r = sum(a * b for a, b in zip(x, r_int(P, c)))
                lam0, psi0 = sectors["plain"]
                lam1, psi1 = sectors["alpha"]
                self.assertEqual(wk, prod_u[0])
                self.assertEqual(psi0, psi)
                self.assertEqual(psi1, s["alpha"] * psi % Q)
                self.assertEqual(lam0, (wk * kappa + r * psi) % Q)
                self.assertEqual(lam1, s["alpha"] * lam0 % Q)
                self.assertLessEqual(abs(r - c_x(P, x)), P.t_bound)

    def test_collapsed_formulas_equal_the_literal_sums_in_the_lean_text(self):
        for name in ("default", "smallest", "wide gates"):
            with self.subTest(name):
                P, gates, x, G, qap, c, ct = self.setup(name, 4)
                v = forms_of(P, ct.seed)
                coeffs, w, wk = reference.lambda_coeffs(P, qap, v, x)
                self.assertTrue(reference.ties_hold(P, v, coeffs["W"]))
                _, sectors = honest_parts(G, P, qap, ct, x)
                for sector in ("plain", "alpha"):
                    self.assertEqual(reference.eval_comb(G, P, ct, sector, **coeffs), sectors[sector][0])
                    self.assertEqual(reference.psi(G, P, ct, w, x, sector), sectors[sector][1])

    def test_the_search_range_always_contains_the_answer(self):
        rng = random.Random(5)
        for name, kw in PARAM_SETS.items():
            P = Params.toy(**kw)
            for w0 in (0, P.g - 1):
                for fill in (0, P.g - 1):
                    for bits in (0, 1):
                        c = Coins(y=[], w0=w0, w=[fill] * P.N, s={})
                        x = [1] + [bits] * P.N
                        r = sum(a * b for a, b in zip(x, r_int(P, c)))
                        self.assertLessEqual(abs(r - c_x(P, x)), P.t_bound, (name, w0, fill, bits))
            for _ in range(50):
                c = Coins(y=[], w0=rng.randrange(P.g), w=[rng.randrange(P.g) for _ in range(P.N)], s={})
                x = [1] + [rng.randrange(2) for _ in range(P.N)]
                r = sum(a * b for a, b in zip(x, r_int(P, c)))
                self.assertLessEqual(abs(r - c_x(P, x)), P.t_bound)


class RoundTrips(unittest.TestCase):
    def test_a_solution_opens_the_lock_for_every_parameter_set(self):
        rng = random.Random(6)
        G = ExponentGroups()
        for name, kw in PARAM_SETS.items():
            P = Params.toy(**kw)
            for trial in range(4):
                with self.subTest(name, trial=trial):
                    gates, x = random_statement(P, rng)
                    message = rng.randbytes(P.message_bytes)
                    ct = encrypt(G, P, gates, message)
                    self.assertEqual(ct.counts(), (P.count_g1(), P.count_g2()))
                    self.assertEqual(decrypt(G, P, gates, ct, x), message)

    def test_every_solution_of_a_statement_opens_it(self):
        P = Params.toy()
        G = ExponentGroups()
        gates = pad(P, [xor_equals(1, 2, 1)])  # many solutions
        message = secrets.token_bytes(32)
        ct = encrypt(G, P, gates, message)
        opened = 0
        for bits in range(16):
            x = [1] + [(bits >> k) & 1 for k in range(4)]
            if is_witness(P, gates, x):
                self.assertEqual(decrypt(G, P, gates, ct, x), message)
                opened += 1
            else:
                self.assertIsNone(decrypt(G, P, gates, ct, x))
        self.assertEqual(opened, 8)

    def test_a_secret_locked_to_a_factorisation_opens_only_for_the_factors(self):
        """The textbook use: only someone who can factor 47,053 can read the message."""
        from praxis_lock import number_bits, product_equals
        P = Params.toy(N=16, gates=1, U=2, n=8, d=2, K1=2, g=7)
        G = ExponentGroups()
        a_bits, b_bits = list(range(1, 9)), list(range(9, 17))
        gates = pad(P, [product_equals(a_bits, b_bits, 211 * 223)])
        message = secrets.token_bytes(32)
        ct = encrypt(G, P, gates, message)

        def solution(a, b):
            return [1] + number_bits(a, 8) + number_bits(b, 8)

        self.assertEqual(decrypt(G, P, gates, ct, solution(211, 223)), message)
        self.assertEqual(decrypt(G, P, gates, ct, solution(223, 211)), message)
        for a, b in ((211, 222), (1, 255), (210, 224), (0, 0), (255, 255)):
            self.assertIsNone(decrypt(G, P, gates, ct, solution(a, b)), (a, b))

    def test_a_larger_statement_still_round_trips(self):
        rng = random.Random(9)
        P = Params.toy(N=40, gates=6, U=8, n=32, d=8, K1=4, g=101)
        G = ExponentGroups()
        gates, x = random_statement(P, rng)
        message = rng.randbytes(32)
        ct = encrypt(G, P, gates, message)
        self.assertEqual(decrypt(G, P, gates, ct, x), message)
        flipped = list(x)
        flipped[7] ^= 1
        if not is_witness(P, gates, flipped):
            self.assertIsNone(decrypt(G, P, gates, ct, flipped))

    def test_wrong_solutions_and_wrong_statements_do_not_open_it(self):
        P = Params.toy()
        G = ExponentGroups()
        gates, x = puzzle(P)
        message = secrets.token_bytes(32)
        ct = encrypt(G, P, gates, message)
        for bits in range(16):
            guess = [1] + [(bits >> k) & 1 for k in range(4)]
            if guess != x:
                self.assertIsNone(decrypt(G, P, gates, ct, guess))
        # A true solution of a different statement, used against this ciphertext.
        other = pad(P, [xor_equals(1, 2, 1), and_equals(2, 3, 1), xor_equals(3, 4, 1)])
        other_x = [1, 0, 1, 1, 0]
        self.assertTrue(is_witness(P, other, other_x))
        self.assertNotEqual(decrypt(G, P, other, ct, other_x), message)

    def test_a_damaged_ciphertext_does_not_yield_the_message(self):
        """Damage every element in turn. A solution only touches the coordinates
        where its bit is 1, so damage elsewhere is invisible to that solution."""
        P = Params.toy()
        G = ExponentGroups()
        gates, x = puzzle(P)  # x_1 = 0, every other entry is 1
        message = secrets.token_bytes(32)
        ct = encrypt(G, P, gates, message)
        by_coordinate = {"L1", "L2", "GY", "ZC", "R2"}
        always = {"QP", "RP", "SC", "QB", "RB"}
        mask_hits = mask_total = 0
        for table in (ct.g1, ct.g2):
            for key in list(table):
                kind, original = key[0], table[key]
                table[key] = (original + 1) % Q
                survived = decrypt(G, P, gates, ct, x) == message
                table[key] = original
                if kind in always or (kind in by_coordinate and x[key[-1]] == 1):
                    self.assertFalse(survived, key)
                elif kind in by_coordinate or kind.startswith("tot"):
                    self.assertTrue(survived, key)   # not used by this solution
                else:                                 # GB, GM: a few coefficients are zero
                    mask_total += 1
                    mask_hits += not survived
        self.assertGreater(mask_hits, 0.9 * mask_total)
        self.assertEqual(decrypt(G, P, gates, ct, x), message)

        ct = encrypt(G, P, gates, message)
        ct.gT = (ct.gT + 1) % Q
        self.assertNotEqual(decrypt(G, P, gates, ct, x), message)
        ct = encrypt(G, P, gates, message)
        ct.seed = bytes(32)
        self.assertNotEqual(decrypt(G, P, gates, ct, x), message)

    def test_encryption_is_randomised_and_the_message_is_masked(self):
        P = Params.toy()
        G = ExponentGroups()
        gates, x = puzzle(P)
        message = b"\x00" * 32
        a, b = encrypt(G, P, gates, message), encrypt(G, P, gates, message)
        self.assertNotEqual(a.cm, b.cm)
        self.assertNotEqual(a.cm, message)
        self.assertNotEqual(a.g1, b.g1)


class CoinsAndSearch(unittest.TestCase):
    def test_invalid_coins_are_rejected(self):
        P = Params.toy()
        qap = QAP(P, puzzle(P)[0])
        good = sample_coins(P, qap, random.Random(8))
        self.assertTrue(valid_coins(P, qap, good))

        def variant(**changes):
            return Coins(good.y, good.w0, good.w, {**good.s, **changes})

        self.assertFalse(valid_coins(P, qap, variant(tau=qap.rho)))
        self.assertFalse(valid_coins(P, qap, variant(tau=qap.xs[-1])))
        self.assertFalse(valid_coins(P, qap, variant(eps=0)))
        self.assertFalse(valid_coins(P, qap, variant(eps=(-inv(pow(P.beta, 2, Q))) % Q)))
        for name in SCALAR_COINS:
            expected = name in ("tau", "Om12")  # the only coins allowed to be zero
            self.assertEqual(valid_coins(P, qap, variant(**{name: 0})), expected, name)

    def test_search_finds_every_value_in_range_and_nothing_outside(self):
        G = ExponentGroups()
        base, bound = 123456789, 40
        for t in (-40, -39, -1, 0, 1, 17, 39, 40):
            self.assertEqual(find_small_multiple(G, t * base % Q, base, bound), t)
        for t in (-41, 41, 1000):
            self.assertIsNone(find_small_multiple(G, t * base % Q, base, bound))
        self.assertEqual(find_small_multiple(G, 0, 0, bound), 0)
        self.assertIsNone(find_small_multiple(G, 5, 0, bound))


@unittest.skipUnless(HAVE_PAIRINGS, "needs py_ecc for real BN254 pairings")
class RealPairings(unittest.TestCase):
    def test_round_trip_on_the_bn254_curve(self):
        P = Params.toy()
        G = BN254Groups()
        gates, x = puzzle(P)
        message = b"a key piece for a Bitcoin vault."
        ct = encrypt(G, P, gates, message)
        self.assertEqual(decrypt(G, P, gates, ct, x), message)
        self.assertIsNone(decrypt(G, P, gates, ct, [1, 1, 1, 1, 1]))
        # Nothing in the ciphertext is a bare exponent: every element is a curve point.
        self.assertTrue(all(isinstance(p, tuple) for p in ct.g1.values()))
        self.assertTrue(all(isinstance(p, tuple) for p in ct.g2.values()))


if __name__ == "__main__":
    unittest.main()
