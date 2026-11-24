"""Tests for the compiler that turns BN254 arithmetic into lock gates.

Three levels: the foreign-field multiplication that everything rests on
(checked exhaustively on miniature instances), the pairing computation
(checked against the py_ecc library), and the Groth16 verifier (checked with
real proofs).

These show that honest values satisfy the gates and that the tampering tried
here does not. They are not a soundness proof; the README lists known gaps.
"""
import importlib.util
import itertools
import random
import secrets
import unittest
from collections import defaultdict

from praxis_lock import ExponentGroups, decrypt, encrypt, is_witness
from praxis_lock.encoding import (Circuit, Lin, P, alloc_big, fp_alloc, fp_assert, fp_const,
                                  fq2_alloc, fq2_assert, fq12_alloc, fq12_assert)
from praxis_lock.encoding.tolock import lock_statement
from praxis_lock.field import Q as R

HAVE_PAIRINGS = importlib.util.find_spec("py_ecc") is not None


def holds(gates, x) -> bool:
    """Evaluate a materialised gate system on an assignment, as the lock would."""
    for g in gates:
        lhs = sum(sum(k * x[i] for i, k in l.items()) * sum(k * x[i] for i, k in r.items())
                  for l, r in zip(g.left, g.right))
        if (lhs - sum(k * x[i] for i, k in g.linear.items())) % R:
            return False
    return True


def bits_free_to_flip(gates, x) -> list:
    """Indices of bits that can be flipped, alone, without any gate noticing."""
    dot = lambda form: sum(k * x[i] for i, k in form.items())
    touching, honest = defaultdict(list), []
    for number, g in enumerate(gates):
        honest.append(([dot(l) for l in g.left], [dot(r) for r in g.right], dot(g.linear)))
        for i in set().union(g.linear, *g.left, *g.right):
            touching[i].append(number)
    free = []
    for i in range(1, len(x)):
        step = 1 - 2 * x[i]
        for number in touching[i]:
            g, (left, right, linear) = gates[number], honest[number]
            lhs = sum((left[j] + step * g.left[j].get(i, 0)) * (right[j] + step * g.right[j].get(i, 0))
                      for j in range(len(left)))
            if (lhs - linear - step * g.linear.get(i, 0)) % R:
                break
        else:
            free.append(i)
    return free


def mini_element(c, value, bits):
    return Lin([(1, alloc_big(c, value, bits))])


class ForeignFieldArithmetic(unittest.TestCase):
    def exhaust(self, build, n_elements, bits, relation):
        """Try every assignment of every bit. Returns the set of element tuples
        that some satisfying assignment carries."""
        c = build([1] * n_elements)
        seen = set()
        for assignment in itertools.product((0, 1), repeat=c.n_bits):
            x = (1,) + assignment
            if holds(c.gates, x):
                values = tuple(sum(x[1 + e * bits + t] << t for t in range(bits))
                               for e in range(n_elements))
                self.assertTrue(relation(*values), values)   # nothing false is accepted
                seen.add(values)
        every = set(itertools.product(range(1 << bits), repeat=n_elements))
        self.assertEqual(seen, {v for v in every if relation(*v)})   # everything true is
        return c

    def test_multiplication_is_exact_on_every_assignment_of_a_miniature(self):
        for p, bits, limb in ((3, 2, 1), (5, 3, 2)):
            def build(values, p=p, bits=bits, limb=limb):
                c = Circuit(materialize=True, limb_bits=limb)
                a, b, out = (mini_element(c, v, bits) for v in values)
                fp_assert(c, [(1, a, b)], [(-1, out)], p=p)
                return c
            with self.subTest(p=p, limb_bits=limb):
                self.exhaust(build, 3, bits, lambda a, b, out, p=p: (a * b - out) % p == 0)

    def test_negative_terms_and_small_multiples_on_a_miniature(self):
        def build(values):
            c = Circuit(materialize=True, limb_bits=1)
            a, b, d = (mini_element(c, v, 2) for v in values)
            fp_assert(c, [(1, a, b), (-2, d, d)], [(1, a)], p=3)     # a b - 2 d^2 + a = 0 mod 3
            return c
        self.exhaust(build, 3, 2, lambda a, b, d: (a * b - 2 * d * d + a) % 3 == 0)

    def test_bn254_multiplication_and_its_size(self):
        rng = random.Random(1)
        for _ in range(20):
            a, b = rng.randrange(P), rng.randrange(P)
            for product, expected in ((a * b % P, True), ((a * b + 1) % P, False)):
                c = Circuit(materialize=True)
                x, y, z = fp_alloc(c, a), fp_alloc(c, b), fp_alloc(c, product)
                fp_assert(c, [(1, x, y)], [(-1, z)])
                self.assertEqual(c.satisfied, expected)
                # The builder's verdict is the gates' verdict, evaluated independently.
                self.assertEqual(holds(c.gates, c.x), expected)
        self.assertEqual((c.n_gates, c.max_products), (5, 3))
        self.assertLess(c.n_bits, 1400)

    def test_extreme_operands(self):
        for a, b in ((0, 0), (P - 1, P - 1), (2**254 - 1, 2**254 - 1), (1, P - 1), (0, 2**254 - 1)):
            c = Circuit()
            x = Lin([(1, alloc_big(c, a, 254))])
            y = Lin([(1, alloc_big(c, b, 254))])
            z = fp_alloc(c, a * b)
            fp_assert(c, [(1, x, y)], [(-1, z)])
            self.assertTrue(c.satisfied, (a, b))

    def test_every_single_bit_of_the_honest_assignment_matters(self):
        rng = random.Random(2)
        a, b = rng.randrange(P), rng.randrange(P)
        c = Circuit(materialize=True)
        x, y, z = fp_alloc(c, a), fp_alloc(c, b), fp_alloc(c, a * b)
        fp_assert(c, [(1, x, y)], [(-1, z)])
        self.assertTrue(holds(c.gates, c.x))
        for i in range(1, len(c.x)):
            flipped = list(c.x)
            flipped[i] ^= 1
            self.assertFalse(holds(c.gates, flipped), i)
        self.assertEqual(bits_free_to_flip(c.gates, c.x), [])      # the fast check agrees

    def test_the_fast_flip_check_finds_a_bit_that_nothing_constrains(self):
        c = Circuit(materialize=True)
        x, y = fp_alloc(c, 3), fp_alloc(c, 5)
        fp_assert(c, [(1, x, x)], [(-1, fp_const(c, 9))])           # y appears in no gate
        free = bits_free_to_flip(c.gates, c.x)
        self.assertEqual(free, list(range(255, 509)))

    def test_the_statement_does_not_depend_on_the_values_assigned(self):
        def build(a, b, out, target=None):
            c = Circuit(materialize=True)
            x, y, z = fp_alloc(c, a), fp_alloc(c, b), fp_alloc(c, out)
            fp_assert(c, [(1, x, y)], [(-1, z)])
            if target is not None:
                fp_assert(c, [], [(1, z), (-1, fp_const(c, target))])
            return c
        one, two = build(3, 5, 15), build(P - 2, 12345, 99)
        self.assertEqual(one.gates, two.gates)
        self.assertEqual(one.fingerprint(), two.fingerprint())
        self.assertNotEqual(one.x, two.x)
        # A different constant in the statement is a different statement.
        self.assertNotEqual(build(3, 5, 15, target=15).fingerprint(),
                            build(3, 5, 15, target=16).fingerprint())

    def test_limbs_too_wide_for_the_locks_field_are_refused_when_building(self):
        """The limb equations must stay far below r, or 'equal modulo r' would
        no longer mean 'equal'. Limbs of 126 bits multiply to 252 bits, and a
        few of those added together pass r / 2."""
        for limb_bits in (126, 200, 254, 300):     # the last two put everything in one limb
            c = Circuit(limb_bits=limb_bits)
            x, y, z = fp_alloc(c, 3), fp_alloc(c, 5), fp_alloc(c, 15)
            with self.assertRaises(OverflowError):
                fp_assert(c, [(1, x, y)], [(-1, z)])

    def test_values_wider_than_their_bits_are_refused(self):
        c = Circuit()
        alloc_big(c, 2**254, 254)
        self.assertFalse(c.satisfied)
        for value in (4, -1):
            c = Circuit()
            c.alloc(value, 2)
            self.assertFalse(c.satisfied)

    def test_a_statement_about_bn254_goes_through_the_lock(self):
        """Lock a secret to "I know a square root of T modulo p"."""
        rng = random.Random(3)
        root = rng.randrange(P)
        target = root * root % P

        def build(y):
            c = Circuit(materialize=True)
            value = fp_alloc(c, y)
            fp_assert(c, [(1, value, value)], [(-1, fp_const(c, target))])
            return c

        honest = build(root)
        params, statement, x = lock_statement(honest)
        self.assertTrue(is_witness(params, statement, x))
        G = ExponentGroups()
        message = secrets.token_bytes(32)
        ct = encrypt(G, params, statement, message)
        self.assertEqual(decrypt(G, params, statement, ct, x), message)
        self.assertEqual(decrypt(G, params, statement, ct, build(P - root).x), message)
        wrong = build((root + 1) % P)
        self.assertFalse(wrong.satisfied)
        self.assertIsNone(decrypt(G, params, statement, ct, wrong.x))


@unittest.skipUnless(HAVE_PAIRINGS, "needs py_ecc")
class TowerAndPairing(unittest.TestCase):
    def test_extension_field_products_match_the_library(self):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding.pairing import ints
        rng = random.Random(4)
        a2 = bn.FQ2([rng.randrange(P), rng.randrange(P)])
        b2 = bn.FQ2([rng.randrange(P), rng.randrange(P)])
        a12 = bn.FQ12([rng.randrange(P) for _ in range(12)])
        b12 = bn.FQ12([rng.randrange(P) for _ in range(12)])
        for right, expected in ((a2 * b2, True), (a2 * b2 + bn.FQ2([1, 0]), False)):
            c = Circuit()
            fq2_assert(c, [(1, fq2_alloc(c, ints(a2)), fq2_alloc(c, ints(b2)))],
                       [(-1, fq2_alloc(c, ints(right)))])
            self.assertEqual(c.satisfied, expected)
        for right, expected in ((a12 * b12, True), (a12 * b12 + bn.FQ12([0] * 11 + [1]), False)):
            c = Circuit()
            fq12_assert(c, [(1, fq12_alloc(c, ints(a12)), fq12_alloc(c, ints(b12)))],
                        [(-1, fq12_alloc(c, ints(right)))])
            self.assertEqual(c.satisfied, expected)

    def test_points_off_the_curve_are_refused(self):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        g1, g2 = bn.multiply(bn.G1, 5), bn.multiply(bn.G2, 7)
        for build, good, bad in (
            (pg.alloc_g1, g1, (g1[0], g1[1] + 1)),
            (pg.alloc_g2, g2, (g2[0], g2[1] + bn.FQ2([1, 0]))),
        ):
            c = Circuit()
            build(c, good)
            self.assertTrue(c.satisfied)
            c = Circuit()
            build(c, bad)
            self.assertFalse(c.satisfied)

    def test_miller_loop_gates_reproduce_the_library_value(self):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import fq12_value
        from praxis_lock.encoding import pairing as pg
        rng = random.Random(5)
        a, b, d, k = (rng.randrange(1, bn.curve_order) for _ in range(4))
        A, B = bn.multiply(bn.G1, a), bn.multiply(bn.G2, b)
        D, C = bn.multiply(bn.G2, d), bn.multiply(bn.G1, k)
        c = Circuit()
        acc = pg.miller_product(c, [(pg.alloc_g2(c, B), pg.alloc_g1(c, A)),
                                    (pg.const_g2(c, D), pg.alloc_g1(c, C).neg())])
        self.assertTrue(c.satisfied, c.failures[:3])
        reference = pg.miller_reference(B, A) * pg.miller_reference(D, bn.neg(C))
        self.assertEqual(acc.val, reference)
        self.assertEqual(fq12_value(acc.lin), pg.ints(reference))

    def test_pairing_equation_accepts_true_products_and_rejects_false_ones(self):
        """e(aG1, bG2) = e(G1, G2)^(ab): true for the right exponent, false otherwise."""
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        a, b = 1234567, 7654321
        A, B = bn.multiply(bn.G1, a), bn.multiply(bn.G2, b)
        one = bn.FQ12.one()
        for scalar, expected in ((a * b, True), (a * b + 1, False)):
            c = Circuit()
            acc = pg.miller_product(c, [(pg.alloc_g2(c, B), pg.alloc_g1(c, A))])
            other = pg.miller_reference(bn.G2, bn.multiply(bn.G1, scalar % bn.curve_order))
            pg.assert_pairing_identity(c, acc, one, other)
            self.assertEqual(c.satisfied, expected)


@unittest.skipUnless(HAVE_PAIRINGS, "needs py_ecc")
class EveryCheckIsNeeded(unittest.TestCase):
    """A shortened pairing computation, small enough to take apart.

    The prover supplies many helper values (slopes, new points, running
    products, a root). For each one, a wrong value is swapped in and everything
    after it is computed consistently from the wrong value. The only thing that
    can then object is the gate meant to check that value, so it must.
    """

    @classmethod
    def setUpClass(cls):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        cls.bn, cls.pg = bn, pg
        cls.A, cls.B = bn.multiply(bn.G1, 5), bn.multiply(bn.G2, 7)
        cls.D, cls.C = bn.multiply(bn.G2, 11), bn.multiply(bn.G1, 13)
        cls.numer = pg.miller_reference(bn.multiply(bn.G2, 3), bn.G1, "1")
        cls.denom = pg.miller_reference(bn.G2, bn.multiply(bn.G1, 9), "1")

    def miller(self, tamper=None, materialize=False, root=False):
        """One doubling, one addition and the closing steps, for a proof pair
        and a fixed pair; optionally followed by a root check with exponent 19."""
        pg = self.pg
        c = Circuit(materialize=materialize)
        c.tamper = tamper
        acc = pg.miller_product(c, [(pg.alloc_g2(c, self.B), pg.alloc_g1(c, self.A)),
                                    (pg.const_g2(c, self.D), pg.alloc_g1(c, self.C).neg())], loop="1")
        if root:
            pg.assert_pairing_identity(c, acc, self.numer, self.denom, order=19)
        return c, acc

    def root_only(self, tamper=None):
        pg = self.pg
        c = Circuit()
        c.tamper = tamper
        acc = pg.Acc()
        acc.val = self.numer * self.denom
        acc.lin = fq12_alloc(c, pg.ints(acc.val))
        pg.assert_pairing_identity(c, acc, self.numer, self.denom, order=19)
        return c

    @staticmethod
    def wrong(label, occurrence):
        bump = lambda v: v + 1 if isinstance(v, int) else v + type(v).one()
        return lambda l, n, v: bump(v) if (l, n) == (label, occurrence) else v

    def test_the_short_loop_matches_the_library(self):
        bn, pg = self.bn, self.pg
        c, acc = self.miller()
        self.assertTrue(c.satisfied, c.failures[:3])
        self.assertEqual(acc.val, pg.miller_reference(self.B, self.A, "1")
                         * pg.miller_reference(self.D, bn.neg(self.C), "1"))

    def test_every_helper_value_in_the_miller_loop_is_checked(self):
        honest, _ = self.miller()
        hints = dict(honest._hints)
        self.assertEqual(len(hints), 18)
        for label, count in sorted(hints.items()):
            for occurrence in range(count):
                with self.subTest(label, occurrence=occurrence):
                    c, _ = self.miller(self.wrong(label, occurrence))
                    self.assertIn(label, c.failures)
                    # Same statement as before; only the answer changed.
                    self.assertEqual(c.fingerprint(), honest.fingerprint())

    def test_every_helper_value_in_the_root_check_is_checked(self):
        honest = self.root_only()
        self.assertTrue(honest.satisfied, honest.failures[:3])
        self.assertEqual(honest._hints, {"root": 1, "root: square": 4, "root: multiply": 2})
        for label, count in sorted(honest._hints.items()):
            for occurrence in range(count):
                with self.subTest(label, occurrence=occurrence):
                    c = self.root_only(self.wrong(label, occurrence))
                    self.assertIn("pairing equation" if label == "root" else label, c.failures)
                    self.assertIn("pairing equation", c.failures)

    def test_no_bit_of_a_whole_pairing_computation_is_left_free(self):
        c, _ = self.miller(materialize=True, root=True)
        self.assertTrue(c.satisfied, c.failures[:3])
        self.assertGreater(c.n_bits, 200_000)
        self.assertTrue(holds(c.gates, c.x))
        self.assertEqual(bits_free_to_flip(c.gates, c.x), [])

    def test_the_builders_verdict_is_the_gates_verdict(self):
        """With a wrong helper value, the stored gates evaluated on the stored
        bits, by code that shares nothing with the builder, also say no."""
        c, _ = self.miller(self.wrong("add: x", 1), materialize=True)
        self.assertFalse(c.satisfied)
        self.assertFalse(holds(c.gates, c.x))


@unittest.skipUnless(HAVE_PAIRINGS, "needs py_ecc")
class Groth16Verifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from praxis_lock.encoding import groth16 as g
        cls.g = g
        cls.r1cs = g.factoring_r1cs()
        cls.vk, cls.trapdoor = g.setup(cls.r1cs)
        cls.proof = g.prove(cls.r1cs, cls.trapdoor, [1, 211 * 223, 211, 223, 211 * 211])
        cls.honest = g.encode_verifier(cls.vk, [47053], cls.proof)

    def test_the_toy_proof_system_itself_is_sound_on_these_cases(self):
        g = self.g
        self.assertTrue(g.verify(self.vk, [47053], self.proof))
        self.assertFalse(g.verify(self.vk, [47054], self.proof))
        with self.assertRaises(ValueError):
            g.prove(self.r1cs, self.trapdoor, [1, 47053, 211, 222, 211 * 211])

    def test_a_real_proof_satisfies_the_verifier_gates(self):
        c = self.honest
        self.assertTrue(c.satisfied, c.failures[:3])
        self.assertLess(c.max_products, 128)
        # Sizes, pinned so that changes to the encoding are noticed.
        self.assertTrue(8_000_000 < c.n_bits < 9_000_000, c.n_bits)
        self.assertTrue(50_000 < c.n_gates < 53_000, c.n_gates)

    def test_a_fresh_proof_of_the_same_statement_also_satisfies_them(self):
        other = self.g.prove(self.r1cs, self.trapdoor, [1, 47053, 223, 211, 223 * 223])
        self.assertNotEqual(other, self.proof)
        c = self.g.encode_verifier(self.vk, [47053], other)
        self.assertTrue(c.satisfied)
        # Same statement: the gates depend on the verifying key and the public
        # input, never on the proof that is being checked.
        self.assertEqual(c.fingerprint(), self.honest.fingerprint())

    def test_wrong_statements_and_forged_proofs_do_not(self):
        from py_ecc import bn128 as bn
        g, (A, B, C) = self.g, self.proof
        cases = {
            "wrong public input": ([47054], (A, B, C)),
            "forged C": ([47053], (A, B, bn.multiply(bn.G1, 12345))),
            "forged A": ([47053], (bn.multiply(bn.G1, 999), B, C)),
            "forged B": ([47053], (A, bn.multiply(bn.G2, 31337), C)),
            "A and C swapped": ([47053], (C, B, A)),
        }
        for name, (public, proof) in cases.items():
            with self.subTest(name):
                self.assertFalse(g.verify(self.vk, public, proof))
                c = g.encode_verifier(self.vk, public, proof)
                self.assertFalse(c.satisfied)
                # A forged proof is a wrong answer to the same statement; a
                # different public input is a different statement.
                same_statement = c.fingerprint() == self.honest.fingerprint()
                self.assertEqual(same_statement, public == [47053])


if __name__ == "__main__":
    unittest.main()
