"""Tests for the compiler that turns BN254 arithmetic into lock gates.

Three levels: the foreign-field multiplication that everything rests on
(checked exhaustively on miniature instances, in both layouts), the pairing
computation (checked against the py_ecc library), and the Groth16 verifier
(checked with real proofs, in every combination of layout and final check).

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
from praxis_lock.encoding import (Circuit, Lin, P, alloc_big, emulated, fp_alloc, fp_assert,
                                  fp_const, fq2_alloc, fq2_assert, fq12_alloc, fq12_assert)
from praxis_lock.encoding.tolock import lock_statement
from praxis_lock.field import Q as R

HAVE_PAIRINGS = importlib.util.find_spec("py_ecc") is not None
STYLES = ("wide", "narrow")


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
        """Try every assignment of every bit: nothing false may pass, and
        everything true must have some assignment that passes."""
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
        cases = [("narrow", 3, 2, 1), ("narrow", 5, 3, 2),
                 ("wide", 3, 2, None), ("wide", 5, 3, None), ("wide", 7, 3, None)]
        for style, p, bits, limb in cases:
            def build(values, style=style, p=p, bits=bits, limb=limb):
                c = Circuit(materialize=True, limb_bits=limb or 85, style=style)
                a, b, out = (mini_element(c, v, bits) for v in values)
                fp_assert(c, [(1, a, b)], [(-1, out)], p=p)
                return c
            with self.subTest(style=style, p=p):
                self.exhaust(build, 3, bits, lambda a, b, out, p=p: (a * b - out) % p == 0)

    def test_negative_terms_squares_and_small_multiples_on_a_miniature(self):
        for style in STYLES:
            def build(values, style=style):
                c = Circuit(materialize=True, limb_bits=1, style=style)
                a, b, d = (mini_element(c, v, 2) for v in values)
                fp_assert(c, [(1, a, b), (-2, d, d)], [(1, a)], p=3)     # a b - 2 d^2 + a = 0 mod 3
                return c
            with self.subTest(style=style):
                self.exhaust(build, 3, 2, lambda a, b, d: (a * b - 2 * d * d + a) % 3 == 0)

    def test_bn254_multiplication_and_its_size(self):
        rng = random.Random(1)
        for style in STYLES:
            for _ in range(10):
                a, b = rng.randrange(P), rng.randrange(P)
                for product, expected in ((a * b % P, True), ((a * b + 1) % P, False)):
                    c = Circuit(materialize=True, style=style)
                    x, y, z = fp_alloc(c, a), fp_alloc(c, b), fp_alloc(c, product)
                    fp_assert(c, [(1, x, y)], [(-1, z)])
                    self.assertEqual(c.satisfied, expected)
                    # The builder's verdict is the gates' verdict, evaluated independently.
                    self.assertEqual(holds(c.gates, c.x), expected)
            helper_bits = c.n_bits - 3 * 254
            if style == "wide":
                self.assertEqual((c.n_gates, c.max_products), (2, 254))
                self.assertLess(helper_bits, 40)
            else:
                self.assertEqual((c.n_gates, c.max_products), (5, 3))
                self.assertLess(helper_bits, 640)

    def test_mixed_claims_agree_with_independent_evaluation(self):
        """Random claims with products, squares, constants and linear terms:
        the builder says yes exactly when the claim is true, and the stored
        gates, evaluated by code that shares nothing with it, say the same."""
        rng = random.Random(6)
        for style in STYLES:
            for trial in range(12):
                values = [rng.randrange(P) for _ in range(4)]
                k1, k2 = rng.randrange(-90, 90), rng.randrange(1, 90)
                constant = rng.randrange(P)
                a, b, d, e = values
                target = (k1 * a * b + 4 * b * a + k2 * d * d + 3 * e * constant + 2 * 7 * constant
                          - 5 * a + e + 11) % P
                for shift, expected in ((0, True), (1, False)):
                    c = Circuit(materialize=True, style=style)
                    xs = [fp_alloc(c, v) for v in values]
                    out = fp_alloc(c, (target + shift) % P)
                    fp_assert(c, [(k1, xs[0], xs[1]), (k2, xs[2], xs[2]),
                                  (4, xs[1], xs[0]),                          # the same product, turned round
                                  (3, xs[3], fp_const(c, constant)),          # number times constant
                                  (1, fp_const(c, constant), xs[3]),          # constant times number
                                  (-1, xs[3], fp_const(c, constant)),         # (these two cancel)
                                  (2, fp_const(c, 7), fp_const(c, constant))],  # constant times constant
                              [(-5, xs[0]), (1, xs[3]), (-1, out), (1, fp_const(c, 11))])
                    self.assertEqual(c.satisfied, expected, (style, trial))
                    self.assertEqual(holds(c.gates, c.x), expected, (style, trial))

    def test_a_carry_below_zero_is_handled(self):
        """In the wide layout the carry between the two halves can be negative,
        so it is stored with an offset. Modulo 7 the constant for bit 2 is 4,
        all of it in the high half; seven such terms add to 28 = 4 * 7, the low
        half comes to 0, and the carry is 0 - 4 * 3 over 4, which is -3."""
        for count, expected in ((7, True), (6, False), (14, True)):
            c = Circuit(materialize=True, style="wide")
            pairs = [(mini_element(c, 4, 3), mini_element(c, 1, 3)) for _ in range(count)]
            fp_assert(c, [(1, a, b) for a, b in pairs], [], p=7)
            self.assertEqual(c.satisfied, expected, count)
            self.assertEqual(holds(c.gates, c.x), expected, count)

    def test_extreme_operands(self):
        for style in STYLES:
            for a, b in ((0, 0), (P - 1, P - 1), (2**254 - 1, 2**254 - 1), (1, P - 1), (0, 2**254 - 1)):
                c = Circuit(materialize=True, style=style)
                x = Lin([(1, alloc_big(c, a, 254))])
                y = Lin([(1, alloc_big(c, b, 254))])
                z = fp_alloc(c, a * b)
                fp_assert(c, [(1, x, y)], [(-1, z)])
                self.assertTrue(c.satisfied, (style, a, b))
                self.assertTrue(holds(c.gates, c.x), (style, a, b))

    def test_every_single_bit_of_the_honest_assignment_matters(self):
        rng = random.Random(2)
        a, b = rng.randrange(P), rng.randrange(P)
        for style in STYLES:
            c = Circuit(materialize=True, style=style)
            x, y, z = fp_alloc(c, a), fp_alloc(c, b), fp_alloc(c, a * b)
            fp_assert(c, [(1, x, y)], [(-1, z)])
            self.assertTrue(holds(c.gates, c.x))
            self.assertEqual(bits_free_to_flip(c.gates, c.x), [])
            # The fast check above, confirmed the slow way on a spread of bits.
            for i in range(1, len(c.x), 1 if style == "narrow" else 37):
                flipped = list(c.x)
                flipped[i] ^= 1
                self.assertFalse(holds(c.gates, flipped), (style, i))

    def test_the_fast_flip_check_finds_a_bit_that_nothing_constrains(self):
        for style in STYLES:
            c = Circuit(materialize=True, style=style)
            x, y = fp_alloc(c, 3), fp_alloc(c, 5)
            fp_assert(c, [(1, x, x)], [(-1, fp_const(c, 9))])           # y appears in no gate
            self.assertEqual(bits_free_to_flip(c.gates, c.x), list(range(255, 509)))

    def test_the_statement_does_not_depend_on_the_values_assigned(self):
        for style in STYLES:
            def build(a, b, out, target=None):
                c = Circuit(materialize=True, style=style)
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
        self.assertNotEqual(Circuit(style="wide").style, Circuit(style="narrow").style)
        with self.assertRaises(ValueError):
            Circuit(style="medium")

    def test_gates_too_large_for_the_locks_field_are_refused_when_building(self):
        """Every gate must stay far below r, or 'equal modulo r' would no longer
        mean 'equal'. Narrow: limbs of 126 bits multiply to 252 bits, and a few
        of those added together pass r / 2. Wide: a modulus of 480 bits has
        halves of 240 bits, and thousands of those added together pass it too."""
        for limb_bits in (126, 200, 254, 300):     # the last two put everything in one limb
            c = Circuit(limb_bits=limb_bits, style="narrow")
            x, y, z = fp_alloc(c, 3), fp_alloc(c, 5), fp_alloc(c, 15)
            with self.assertRaises(OverflowError):
                fp_assert(c, [(1, x, y)], [(-1, z)])
        big = (1 << 480) + 3
        for bits in (120, 480):                    # few terms: fine. Many: refused.
            c = Circuit(style="wide")
            x, y, z = (Lin([(1, alloc_big(c, v, bits))]) for v in (3, 5, 15))
            if bits == 120:
                fp_assert(c, [(1, x, y)], [(-1, z)], p=big)
                self.assertTrue(c.satisfied)
            else:
                with self.assertRaises(OverflowError):
                    fp_assert(c, [(1, x, y)], [(-1, z)], p=big)

    def test_the_bit_counting_trick_has_room_for_its_counts(self):
        """The wide layout counts pairs of set bits in 32-bit digits. A claim
        with so many products that a count could overflow is refused."""
        saved = emulated._DIGIT_ROOM
        try:
            emulated._DIGIT_ROOM = 200             # pretend digits hold less than one product's worth
            c = Circuit(style="wide")
            x, y, z = fp_alloc(c, 3), fp_alloc(c, 5), fp_alloc(c, 15)
            with self.assertRaises(OverflowError):
                fp_assert(c, [(1, x, y)], [(-1, z)])
        finally:
            emulated._DIGIT_ROOM = saved
        self.assertEqual(saved, 1 << 32)

    def test_the_lock_gets_one_more_point_per_gate_than_the_widest_gate_has_products(self):
        from praxis_lock.encoding.tolock import lock_params
        for products, expected_u in ((1, 2), (2, 4), (3, 4), (4, 8), (7, 8), (8, 16)):
            c = Circuit(materialize=True)
            bits = [c.alloc(1, 1) for _ in range(products)]
            c.gate([(1, b, b) for b in bits], products, [])          # 1 + 1 + ... = products
            self.assertTrue(c.satisfied)
            self.assertEqual(lock_params(c).U, expected_u)

    def test_values_wider_than_their_bits_are_refused(self):
        for style in STYLES:
            c = Circuit(style=style)
            alloc_big(c, 2**254, 254)
            self.assertFalse(c.satisfied)
        for value in (4, -1):
            c = Circuit()
            c.alloc(value, 2)
            self.assertFalse(c.satisfied)

    def test_a_statement_about_bn254_goes_through_the_lock(self):
        """Lock a secret to "I know a square root of T modulo p", in both layouts."""
        rng = random.Random(3)
        root = rng.randrange(P)
        target = root * root % P
        for style in STYLES:
            def build(y):
                c = Circuit(materialize=True, style=style)
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
            if style == "wide":
                self.assertEqual(decrypt(G, params, statement, ct, build(P - root).x), message)
            wrong = build((root + 1) % P)
            self.assertFalse(wrong.satisfied)
            self.assertIsNone(decrypt(G, params, statement, ct, wrong.x))
        self.assertLess(honest.n_bits, 900)


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
        for style in STYLES:
            for right, expected in ((a2 * b2, True), (a2 * b2 + bn.FQ2([1, 0]), False)):
                c = Circuit(style=style)
                fq2_assert(c, [(1, fq2_alloc(c, ints(a2)), fq2_alloc(c, ints(b2)))],
                           [(-1, fq2_alloc(c, ints(right)))])
                self.assertEqual(c.satisfied, expected)
            for right, expected in ((a12 * b12, True), (a12 * b12 + bn.FQ12([0] * 11 + [1]), False)):
                c = Circuit(style=style)
                fq12_assert(c, [(1, fq12_alloc(c, ints(a12)), fq12_alloc(c, ints(b12)))],
                            [(-1, fq12_alloc(c, ints(right)))])
                self.assertEqual(c.satisfied, expected)

    def test_fast_arithmetic_used_by_the_prover_matches_the_library(self):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        rng = random.Random(8)
        x = bn.FQ12([rng.randrange(P) for _ in range(12)])
        y = bn.FQ12([rng.randrange(P) for _ in range(12)])
        self.assertEqual(pg.fq12_mul(x, y), x * y)
        for e in (0, 1, 2, 77, 2**70 + 12345):
            self.assertEqual(pg.fq12_pow(x, e), x ** e)
        self.assertEqual(pg.frobenius_value(x, 1), pg.fq12_pow(x, P))
        self.assertEqual(pg.frobenius_value(x, 3), pg.fq12_pow(x, P**3))
        A, B = bn.multiply(bn.G1, 5), bn.multiply(bn.G2, 7)
        for loop in ("", "0", "1", "1101", pg.LOOP):
            self.assertEqual(pg.miller_value(B, A, loop), pg.miller_reference(B, A, loop))
        self.assertEqual(pg.loop_count(pg.LOOP), pg.ATE)

    def test_points_off_the_curve_are_refused(self):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        g1, g2 = bn.multiply(bn.G1, 5), bn.multiply(bn.G2, 7)
        for style in STYLES:
            for build, good, bad in (
                (pg.alloc_g1, g1, (g1[0], g1[1] + 1)),
                (pg.alloc_g2, g2, (g2[0], g2[1] + bn.FQ2([1, 0]))),
            ):
                c = Circuit(style=style)
                build(c, good)
                self.assertTrue(c.satisfied)
                c = Circuit(style=style)
                build(c, bad)
                self.assertFalse(c.satisfied)

    def test_a_point_in_the_wide_layout_has_no_free_bit(self):
        """Small enough to write out in full: every gate of a G1 point's curve
        check, evaluated independently, with each bit flipped in turn."""
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        c = Circuit(materialize=True, style="wide")
        pg.alloc_g1(c, bn.multiply(bn.G1, 5))
        self.assertTrue(holds(c.gates, c.x))
        self.assertEqual(bits_free_to_flip(c.gates, c.x), [])

    def test_miller_loop_gates_reproduce_the_library_value(self):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import fq12_value
        from praxis_lock.encoding import pairing as pg
        rng = random.Random(5)
        a, b, d, k = (rng.randrange(1, bn.curve_order) for _ in range(4))
        A, B = bn.multiply(bn.G1, a), bn.multiply(bn.G2, b)
        D, C = bn.multiply(bn.G2, d), bn.multiply(bn.G1, k)
        reference = pg.miller_reference(B, A) * pg.miller_reference(D, bn.neg(C))
        for style in STYLES:
            c = Circuit(style=style)
            acc = pg.miller_product(c, [(pg.alloc_g2(c, B), pg.alloc_g1(c, A)),
                                        (pg.const_g2(c, D), pg.alloc_g1(c, C).neg())])
            self.assertTrue(c.satisfied, c.failures[:3])
            self.assertEqual(acc.val, reference)
            self.assertEqual(fq12_value(acc.lin), pg.ints(reference))

    def test_pairing_equation_accepts_true_products_and_rejects_false_ones(self):
        """e(aG1, bG2) = e(G1, G2)^(ab): true for the right exponent, false
        otherwise. Once with a separate root, once folded into the loop."""
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        a, b = 1234567, 7654321
        A, B = bn.multiply(bn.G1, a), bn.multiply(bn.G2, b)
        one = bn.FQ12.one()
        for scalar, expected in ((a * b, True), (a * b + 1, False)):
            other = pg.miller_reference(bn.G2, bn.multiply(bn.G1, scalar % bn.curve_order))
            c = Circuit(style="narrow")
            acc = pg.miller_product(c, [(pg.alloc_g2(c, B), pg.alloc_g1(c, A))])
            pg.assert_pairing_identity(c, acc, one, other)
            self.assertEqual(c.satisfied, expected)
            c = Circuit(style="wide")
            pg.assert_pairing_folded(c, [(pg.alloc_g2(c, B), pg.alloc_g1(c, A))], one, other)
            self.assertEqual(c.satisfied, expected)
            if not expected:
                self.assertEqual(set(c.failures), {"pairing equation"})

    def test_the_number_facts_behind_the_folded_check(self):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        import math
        numbers = pg.fold_numbers()
        n, r, omega = P**12 - 1, bn.curve_order, numbers["omega"]
        self.assertEqual(pg.LAMBDA % r, 0)                        # a lambda-th power is an r-th power
        self.assertEqual(math.gcd(pg.LAMBDA, n), 3 * r)
        self.assertEqual((n // r) % 27, 0)                        # so omega^h = 1
        self.assertEqual(pg.fq12_pow(omega, 27), bn.FQ12.one())
        self.assertNotEqual(pg.fq12_pow(omega, 9), bn.FQ12.one())
        self.assertEqual(pg.fq12_pow(omega, n // r), bn.FQ12.one())

    def test_the_facts_behind_the_half_size_running_product(self):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        FQ12, rng = bn.FQ12, random.Random(11)
        rand = lambda: FQ12([rng.randrange(P) for _ in range(12)])
        n, r = P**12 - 1, bn.curve_order
        f1, f2, sub = rand(), rand(), pg._split(rand())[0]         # sub lies in the half-size subfield
        # Factors from the subfield vanish under the final exponentiation, so only classes matter.
        self.assertEqual((n // r) % (P**6 - 1), 0)
        self.assertEqual(pg.fq12_pow(sub, n // r), FQ12.one())
        g1, g2 = pg.torus_class(f1), pg.torus_class(f2)
        self.assertEqual(pg._split(g1)[1], FQ12.zero())               # a class is six numbers
        self.assertEqual(pg.torus_class(f1 * sub), g1)
        self.assertEqual(pg.torus_class(f1 * f2), pg.torus_mul(g1, g2))
        self.assertEqual(pg.torus_class(FQ12.one() / f1), FQ12.zero() - g1)
        self.assertEqual(pg.fq6_inverse(sub), FQ12.one() / sub)
        # The three-way choice of the folded check is a subfield factor, so it disappears.
        self.assertEqual(pg._split(pg.fold_numbers()["omega"])[1], FQ12.zero())
        # Raising to p^e acts on classes as a fixed linear map.
        for e, sign in ((1, 1), (2, -1), (3, 1)):
            image = FQ12.zero()
            for coef, column in zip(pg.ints(g1)[0::2], pg._torus_frobenius_basis(e, sign)):
                image = image + column * coef
            source = f1 if sign == 1 else FQ12.one() / f1
            self.assertEqual(image, pg.torus_class(pg.fq12_pow(source, P**e)))
        # Multiplying stored numbers by v is a reshuffle with two small constants.
        c = Circuit()
        full = pg._fq6_alloc(c, sub)
        self.assertEqual([x.value() for x in pg._times_v(full)], pg.ints(pg._V * sub))
        # The signed loop: same count, fewer additions, same class as the library's loop.
        digits = pg.signed_digits(pg.ATE)
        self.assertEqual(pg.signed_count(digits[1:]), pg.ATE)
        self.assertTrue(all(not (a and b) for a, b in zip(digits, digits[1:])))
        self.assertEqual((len(pg.SIGNED_LOOP), sum(1 for d in pg.SIGNED_LOOP if d)), (65, 21))
        A, B = bn.multiply(bn.G1, 5), bn.multiply(bn.G2, 7)
        signed, binary = pg.miller_value(B, A, pg.SIGNED_LOOP), pg.miller_reference(B, A)
        self.assertNotEqual(signed, binary)
        self.assertEqual(pg.torus_class(signed), pg.torus_class(binary))

    def test_the_two_forms_of_a_running_value_and_the_rules_between_them(self):
        """Up to factors from F_p^6, an element a + b w is kept as G = l0 a/b
        (form A) or h = l0 b/a (form B). Each rule's new value must be the form
        of the true product, its gate must accept it, and must refuse another."""
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        rng = random.Random(12)
        x, y = (bn.FQ12([rng.randrange(P) for _ in range(12)]) for _ in range(2))
        plain_l0, imaginary_l0 = bn.FQ2([rng.randrange(1, P), 0]), bn.FQ2([0, rng.randrange(1, P)])
        rules = ["square of A", "square of B", "A times B", "B times B", "A times A",
                 "class from A", "class from B", "form A of a class"]

        def run(l0_v, style, tamper=None):
            c = Circuit(style=style)
            c.tamper = tamper
            forms = pg._Forms(c, fq2_alloc(c, pg.ints(l0_v)), l0_v)
            unit = pg._embed_value(l0_v)
            ratio = lambda top, bottom: pg.fq12_mul(pg.fq12_mul(unit, top), pg.fq6_inverse(bottom))
            form_a = lambda z: ratio(*pg._split(z))
            form_b = lambda z: ratio(*pg._split(z)[::-1])
            bits = lambda v: pg._fq6_alloc(c, v)
            xa, xb, ya, yb, cls = form_a(x), form_b(x), form_a(y), form_b(y), pg.torus_class(x)
            got = [forms.square(bits(xa), xa, "A", rules[0])[1],
                   forms.square(bits(xb), xb, "B", rules[1])[1],
                   forms.a_times_b(bits(xa), xa, bits(yb), yb, rules[2])[1],
                   forms.b_times_b(bits(xb), xb, bits(yb), yb, rules[3])[1],
                   forms.a_times_a(bits(xa), xa, bits(ya), ya, rules[4])[1],
                   forms.plain(bits(xa), xa, "A", rules[5])[1],
                   forms.plain(bits(xb), xb, "B", rules[6])[1],
                   forms.scaled(bits(cls), cls, rules[7])[1]]
            want = [form_a(x * x), form_a(x * x), form_b(x * y), form_a(x * y), form_a(x * y), cls, cls, xa]
            # A running value in either form, times a value in either form, lands in the form reported.
            for form, v in (("A", xa), ("B", xb)):
                for other, w, times in (("A", ya, forms.times_a), ("B", yb, forms.times_b)):
                    _, value, landed = times(bits(v), v, form, bits(w), w, "either form")
                    got.append(value)
                    want.append({"A": form_a, "B": form_b}[landed](x * y))
            # The inverse of an element has the negative form-A value: that is how a digit -1 is handled.
            got.append(form_a(bn.FQ12.one() / x))
            want.append(bn.FQ12.zero() - xa)
            return c, got, want

        for l0_v in (plain_l0, imaginary_l0):
            for style in STYLES:
                c, got, want = run(l0_v, style)
                self.assertEqual(got, want)
                self.assertTrue(c.satisfied, c.failures[:3])
                for label in rules:
                    bump = lambda l, n, v, label=label: v + type(v).one() if l == label else v
                    self.assertEqual(set(run(l0_v, style, bump)[0].failures), {label})
        # The constant term's square must be a plain number; one that is not is refused.
        c = Circuit()
        mixed = bn.FQ2([3, 5])
        pg._Forms(c, fq2_alloc(c, pg.ints(mixed)), mixed)
        self.assertEqual(set(c.failures), {"bundled: constant term squared"})

    def test_bundles_of_fixed_lines_are_the_products_they_stand_for(self):
        """The fixed pairing's lines enter a few steps at a time, as fixed
        polynomials in two numbers of its G1 point. Put together again, with the
        squarings in between, they must give that pairing's Miller value."""
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        D, C = bn.multiply(bn.G2, 11), bn.multiply(bn.G1, 13)
        eta = pow(int(C[1]), -1, P)
        rho = int(C[0]) * eta % P
        self.assertEqual(pow(rho, 3, P), (eta - 3 * pow(eta, 3, P)) % P)     # the curve, in these two numbers
        key = (tuple(pg.ints(D[0])), tuple(pg.ints(D[1])))
        for loop, max_degree in ((tuple(pg.SIGNED_LOOP), pg.BUNDLE_DEGREE), ((0, 1, 0), 4), ((1, -1), 4),
                                 ((1, -1), 30), ((0, 0, -1, 0, 1), 6)):
            plan = pg.bundle_plan(loop, max_degree)
            self.assertEqual([k for steps, _ in plan for k in steps], list(range(len(loop))))
            bundles = pg._bundles(key, loop, max_degree)
            self.assertEqual([last for last, _ in bundles], [steps[-1] for steps, _ in plan])
            total = bn.FQ12.one()
            for (steps, degree), (_, poly) in zip(plan, bundles):
                self.assertLessEqual(degree, max_degree)
                self.assertLessEqual(max(b + e for b, e in poly), degree)
                self.assertLessEqual(max(b for b, _ in poly), 2)
                pg._split_bundle(poly)                               # even part in F_p^6, odd part in F_p^6 w
                value = bn.FQ12.zero()
                for (b, e), coefs in poly.items():
                    value = value + bn.FQ12(coefs) * (pow(rho, b, P) * pow(eta, e, P) % P)
                for _ in steps:
                    total = pg.fq12_mul(total, total)
                total = pg.fq12_mul(total, value)
            self.assertEqual(pg.torus_class(total), pg.torus_class(pg.miller_value(D, C, loop)))
        full = pg.bundle_plan(pg.SIGNED_LOOP)
        self.assertEqual((len(full), max(degree for _, degree in full)), (17, 23))
        with self.assertRaises(ValueError):
            pg._split_bundle({(0, 0): (0, 1) + (0,) * 10})
        with self.assertRaises(ValueError):
            pg._split_bundle({(1, 0): (1,) + (0,) * 11})

    def test_the_prover_finds_a_root_exactly_when_the_pairing_product_is_one(self):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        powers = pg.fold_numbers()["powers"]
        a, b = 1234567, 7654321
        A, B = bn.multiply(bn.G1, a), bn.multiply(bn.G2, b)
        true_product = pg.miller_value(B, A) / pg.miller_value(bn.G2, bn.multiply(bn.G1, a * b))
        root, j0 = pg.residue_witness(true_product)
        self.assertEqual(pg.fq12_pow(root, pg.LAMBDA), true_product * powers[j0])
        # Multiplying by powers of omega keeps it an r-th power and walks through all three choices.
        seen = set()
        for k in range(3):
            f = true_product * powers[-k % 27]
            c, j = pg.residue_witness(f)
            self.assertEqual(j, (j0 + k) % 3)
            self.assertEqual(pg.fq12_pow(c, pg.LAMBDA), f * powers[j])
            seen.add(j)
        self.assertEqual(seen, {0, 1, 2})
        false_product = true_product * pg.miller_value(bn.G2, bn.G1)
        c, j = pg.residue_witness(false_product)
        self.assertNotEqual(pg.fq12_pow(c, pg.LAMBDA), false_product * powers[j])


@unittest.skipUnless(HAVE_PAIRINGS, "needs py_ecc")
class EveryCheckIsNeeded(unittest.TestCase):
    """A shortened pairing computation, small enough to take apart.

    The prover supplies many helper values (slopes, new points, running
    products, a root). For each one, a wrong value is swapped in and everything
    after it is computed consistently from the wrong value. The only thing that
    can then object is the gate meant to check that value, so it must.
    """

    # Helper values with no gate of their own name: the last equation catches them.
    CAUGHT_BY = {"root": "pairing equation", "fold: root": "pairing equation",
                 "fold: choice": "pairing equation", "compact: root": "pairing equation",
                 "bundled: root": "pairing equation"}
    COMPACT_LOOP = [1, -1]              # two doublings, one addition of each sign
    OTHER_LOOP = [0, 1, 0]              # three doublings, one addition: the remaining cases of the bundled check

    @classmethod
    def setUpClass(cls):
        from py_ecc import bn128 as bn
        from praxis_lock.encoding import pairing as pg
        cls.bn, cls.pg = bn, pg
        cls.A, cls.B = bn.multiply(bn.G1, 5), bn.multiply(bn.G2, 7)
        cls.D, cls.C = bn.multiply(bn.G2, 11), bn.multiply(bn.G1, 13)
        cls.numer = pg.miller_reference(bn.multiply(bn.G2, 3), bn.G1, "1")
        cls.denom = pg.miller_reference(bn.G2, bn.multiply(bn.G1, 9), "1")
        # For the folded check on a short loop the test plays prover: it picks
        # the root and sets the constant on the right so that the claim is true.
        rng = random.Random(9)
        cls.fold_root = bn.FQ12([rng.randrange(P) for _ in range(12)])
        cls.fold_miller = pg.miller_reference(cls.B, cls.A, "1") * pg.miller_reference(cls.D, bn.neg(cls.C), "1")
        cls.fold_exponent = pg.loop_count("1") + P - P**2 + P**3
        cls.compact_miller = (pg.miller_value(cls.B, cls.A, cls.COMPACT_LOOP)
                              * pg.miller_value(cls.D, bn.neg(cls.C), cls.COMPACT_LOOP))
        cls.compact_exponent = pg.signed_count(cls.COMPACT_LOOP) + P - P**2 + P**3

    def pairs(self, c):
        pg = self.pg
        return [(pg.alloc_g2(c, self.B), pg.alloc_g1(c, self.A)),
                (pg.const_g2(c, self.D), pg.alloc_g1(c, self.C).neg())]

    def miller(self, tamper=None, materialize=False, root=False, style="narrow"):
        """One doubling, one addition and the closing steps, for a proof pair
        and a fixed pair; optionally followed by a root check with exponent 19."""
        pg = self.pg
        c = Circuit(materialize=materialize, style=style)
        c.tamper = tamper
        acc = pg.miller_product(c, self.pairs(c), loop="1")
        if root:
            pg.assert_pairing_identity(c, acc, self.numer, self.denom, order=19)
        return c, acc

    def root_only(self, tamper=None, style="narrow"):
        pg = self.pg
        c = Circuit(style=style)
        c.tamper = tamper
        acc = pg.Acc()
        acc.val = self.numer * self.denom
        acc.lin = fq12_alloc(c, pg.ints(acc.val))
        pg.assert_pairing_identity(c, acc, self.numer, self.denom, order=19)
        return c

    def folded(self, choice=0, tamper=None, materialize=False, style="narrow"):
        """The same short loop with the final check folded in."""
        pg = self.pg
        omega_j = pg.fold_numbers()["powers"][choice]
        denom = self.fold_miller * self.numer * omega_j / pg.fq12_pow(self.fold_root, self.fold_exponent)
        c = Circuit(materialize=materialize, style=style)
        c.tamper = tamper
        pg.assert_pairing_folded(c, self.pairs(c), self.numer, denom, "1",
                                 witness=(self.fold_root, choice))
        return c

    def compact(self, tamper=None, materialize=False, style="narrow", shift=0, rescale=False):
        """A short signed loop with the compact check. `shift` multiplies the
        right-hand constant by a power of omega, which must make no difference."""
        pg = self.pg
        omega = pg.fold_numbers()["powers"][shift]
        denom = (self.compact_miller * self.numer * omega
                 / pg.fq12_pow(self.fold_root, self.compact_exponent))
        c = Circuit(materialize=materialize, style=style)
        c.tamper = tamper
        moving, fixed = self.pairs(c)
        pg.assert_pairing_compact(c, moving, fixed, self.numer, denom, self.COMPACT_LOOP,
                                  witness=(self.fold_root, 0), rescale=rescale)
        return c

    def bundled(self, tamper=None, materialize=False, style="narrow", loop=None, max_degree=4, shift=0,
                A=None, off=False):
        """A short signed loop with the bundled check, two bundles of fixed lines.
        `shift` multiplies the right-hand constant by a power of omega, which must make no
        difference; `off` multiplies it by something that must."""
        bn, pg = self.bn, self.pg
        loop = self.COMPACT_LOOP if loop is None else loop
        A = self.A if A is None else A
        miller = pg.miller_value(self.B, A, loop) * pg.miller_value(self.D, bn.neg(self.C), loop)
        exponent = pg.signed_count(loop) + P - P**2 + P**3
        denom = miller * self.numer * pg.fold_numbers()["powers"][shift] / pg.fq12_pow(self.fold_root, exponent)
        if off:
            denom = denom * pg.miller_value(bn.G2, bn.G1, "1")
        c = Circuit(materialize=materialize, style=style)
        c.tamper = tamper
        moving = (pg.alloc_g2(c, self.B), pg.alloc_g1(c, A))
        fixed = (pg.const_g2(c, self.D), pg.alloc_g1(c, self.C).neg())
        pg.assert_pairing_bundled(c, moving, fixed, self.numer, denom, loop, witness=(self.fold_root, 0),
                                  max_degree=max_degree)
        return c

    @staticmethod
    def wrong(label, occurrence):
        def bump(v):
            if isinstance(v, tuple):               # the two choice bits: set both
                return (1, 1)
            return v + 1 if isinstance(v, int) else v + type(v).one()
        return lambda l, n, v: bump(v) if (l, n) == (label, occurrence) else v

    def sweep(self, build, expected_labels, only=None, every=None):
        """Swap in a wrong value for every helper value in turn. `only` limits
        the sweep to some labels; `every` thins out the occurrences of others."""
        honest = build(None)
        self.assertTrue(honest.satisfied, honest.failures[:3])
        self.assertEqual(sorted(honest._hints), sorted(expected_labels))
        tried = 0
        for label, count in sorted(honest._hints.items()):
            if only is not None and label not in only:
                continue
            for occurrence in range(0, count, (every or {}).get(label, 1)):
                with self.subTest(label, occurrence=occurrence):
                    c = build(self.wrong(label, occurrence))
                    self.assertFalse(c.satisfied)
                    self.assertIn(self.CAUGHT_BY.get(label, label), c.failures)
                    # Same statement as before; only the answer changed.
                    self.assertEqual(c.fingerprint(), honest.fingerprint())
                    tried += 1
        return tried

    MILLER_LABELS = ["Frobenius x", "Frobenius y", "Frobenius^2 x", "Frobenius^2 y",
                     "G1: x squared", "G2: x squared", "Miller: Frobenius chord", "Miller: chord",
                     "Miller: last chord", "Miller: tangent", "add: slope", "add: x", "add: y",
                     "double: slope", "double: x", "double: y", "line: intercept",
                     "line: slope times x_P"]
    POINT_LABELS = ["Frobenius x", "Frobenius y", "Frobenius^2 x", "Frobenius^2 y", "G1: x squared",
                    "G2: x squared", "add: slope", "add: x", "add: y", "double: slope", "double: x",
                    "double: y", "line: intercept", "line: slope times x_P"]
    COMPACT_LABELS = ["compact: Frobenius 1 product", "compact: Frobenius 2 product", "compact: fold",
                      "compact: lines", "compact: one over y", "compact: root", "compact: square",
                      "compact: step", "compact: x over y", "compact: y times one over y",
                      "compact: y times x over y"]
    NARROW_ONLY = ["compact: Frobenius 1", "compact: Frobenius 2", "compact: Frobenius 3",
                   "compact: constant multiple"]
    RESCALED_LABELS = ["Frobenius x", "Frobenius y", "Frobenius^2 x", "Frobenius^2 y", "G1: x squared",
                       "G2: x squared", "compact: Frobenius 1 product", "compact: Frobenius 2 product",
                       "compact: fold", "compact: lines", "compact: one over y", "compact: root",
                       "compact: square", "compact: step", "compact: x over y",
                       "rescaled: constant term", "rescaled: constant term times one over y",
                       "rescaled: constant term times x over y", "rescaled: new x",
                       "rescaled: on the line", "rescaled: one over x", "rescaled: point x",
                       "rescaled: point y", "rescaled: root of that", "rescaled: tangent",
                       "rescaled: through the other point"]
    BUNDLED_LABELS = ["Frobenius x", "Frobenius y", "Frobenius^2 x", "Frobenius^2 y", "G1: x squared",
                      "G2: x squared", "bundled: class", "bundled: constant term squared",
                      "bundled: fixed lines", "bundled: fixed lines in", "bundled: line", "bundled: power",
                      "bundled: root", "bundled: root in", "bundled: root, scaled", "bundled: square",
                      "compact: Frobenius 1 product", "compact: Frobenius 2 product", "compact: one over y",
                      "compact: x over y", "rescaled: constant term", "rescaled: new x",
                      "rescaled: on the line", "rescaled: one over x", "rescaled: point x",
                      "rescaled: point y", "rescaled: root of that", "rescaled: tangent",
                      "rescaled: through the other point"]
    FOLD_LABELS = ["Miller: fold", "Miller: square", "fold: Frobenius 1", "fold: Frobenius 2",
                   "fold: Frobenius 3", "fold: choice", "fold: first product", "fold: inverse",
                   "fold: one choice", "fold: root", "fold: second product"]

    def test_the_short_loop_matches_the_library(self):
        bn, pg = self.bn, self.pg
        for style in STYLES:
            c, acc = self.miller(style=style)
            self.assertTrue(c.satisfied, c.failures[:3])
            self.assertEqual(acc.val, pg.miller_reference(self.B, self.A, "1")
                             * pg.miller_reference(self.D, bn.neg(self.C), "1"))

    def test_every_helper_value_in_the_miller_loop_is_checked(self):
        for style in STYLES:
            tried = self.sweep(lambda tamper: self.miller(tamper, style=style)[0], self.MILLER_LABELS)
            self.assertEqual(tried, 36)

    def test_every_helper_value_in_the_root_check_is_checked(self):
        for style in STYLES:
            tried = self.sweep(lambda tamper: self.root_only(tamper, style=style),
                               ["root", "root: square", "root: multiply"])
            self.assertEqual(tried, 7)

    def test_every_helper_value_in_the_folded_check_is_checked(self):
        for style in STYLES:
            tried = self.sweep(lambda tamper: self.folded(1, tamper, style=style),
                               self.MILLER_LABELS + self.FOLD_LABELS)
            self.assertEqual(tried, 48)

    def test_every_helper_value_in_the_compact_check_is_checked(self):
        labels = self.POINT_LABELS + self.COMPACT_LABELS
        tried = self.sweep(lambda tamper: self.compact(tamper, style="wide"), labels)
        self.assertEqual(tried, 58)
        # The narrow layout runs the same code and stores a few values more; sweep those.
        tried = self.sweep(lambda tamper: self.compact(tamper, style="narrow"),
                           labels + self.NARROW_ONLY, only=self.NARROW_ONLY,
                           every={"compact: constant multiple": 6})
        self.assertEqual(tried, 11)

    def test_every_helper_value_of_the_y_free_moving_point_is_checked(self):
        tried = self.sweep(lambda tamper: self.compact(tamper, style="wide", rescale=True),
                           self.RESCALED_LABELS)
        self.assertEqual(tried, 56)
        self.assertTrue(self.compact(style="narrow", rescale=True).satisfied)

    def test_every_helper_value_in_the_bundled_check_is_checked(self):
        """Two short loops. Between them the running value is squared, multiplied by a line,
        by the root and by a bundle of fixed lines in each of its two forms."""
        for loop in (self.COMPACT_LOOP, self.OTHER_LOOP):
            tried = self.sweep(lambda tamper: self.bundled(tamper, style="wide", loop=loop), self.BUNDLED_LABELS)
            self.assertEqual(tried, 64)
            self.assertTrue(self.bundled(style="narrow", loop=loop).satisfied)
        # The narrow layout stores each constant multiple inside a bundle as well; sweep some of those.
        tried = self.sweep(lambda tamper: self.bundled(tamper, style="narrow"),
                           self.BUNDLED_LABELS + self.NARROW_ONLY, only=self.NARROW_ONLY,
                           every={"compact: constant multiple": 8})
        self.assertEqual(tried, 7)

    def test_the_bundled_check_does_not_depend_on_how_the_fixed_lines_are_bundled(self):
        """One bundle or two, a true claim passes and a false one fails at the last equation only.
        Multiplying the right-hand constant by a power of omega changes nothing."""
        prints = set()
        for max_degree in (4, 30):
            for shift in (0, 1, 5):
                c = self.bundled(style="wide", max_degree=max_degree, shift=shift)
                self.assertTrue(c.satisfied, c.failures[:3])
                prints.add((max_degree, c.fingerprint()))
            for loop in (self.COMPACT_LOOP, self.OTHER_LOOP):
                c = self.bundled(style="wide", loop=loop, max_degree=max_degree, off=True)
                self.assertEqual(set(c.failures), {"pairing equation"})
        self.assertEqual(len(prints), 2)                # one statement per way of bundling
        pg = self.pg
        self.assertEqual([len(pg.bundle_plan(self.COMPACT_LOOP, d)) for d in (4, 30)], [2, 1])

    def test_the_bundled_check_wants_one_point_of_bits_and_one_constant(self):
        pg = self.pg
        c = Circuit()
        moving, fixed = self.pairs(c)
        for pair in ((moving, moving), (fixed, fixed), (fixed, moving)):
            with self.assertRaises(ValueError):
                pg.assert_pairing_bundled(c, pair[0], pair[1], self.numer, self.denom, self.COMPACT_LOOP,
                                          witness=(self.fold_root, 0))

    def test_no_bit_of_a_bundled_pairing_computation_is_left_free(self):
        c = self.bundled(materialize=True, loop=self.OTHER_LOOP)
        self.assertTrue(c.satisfied, c.failures[:3])
        self.assertGreater(c.n_bits, 200_000)
        self.assertTrue(holds(c.gates, c.x))
        self.assertEqual(bits_free_to_flip(c.gates, c.x), [])
        c = self.bundled(self.wrong("bundled: fixed lines", 1), materialize=True, loop=self.OTHER_LOOP)
        self.assertFalse(c.satisfied)
        self.assertFalse(holds(c.gates, c.x))

    def test_the_y_free_moving_point_works_for_either_kind_of_square_root(self):
        """The rescaling needs a square root of 1 / x_A. It lies in F_p for some
        proofs and only in F_p^2 for others; the same gates must serve both."""
        bn, pg = self.bn, self.pg
        kinds = set()
        for k in range(2, 12):
            A = bn.multiply(bn.G1, k)
            root = pg._sqrt_in_fq2(pow(int(A[0]), -1, P))
            self.assertEqual(root * root, bn.FQ2([pow(int(A[0]), -1, P), 0]))
            kind = "plain" if int(root.coeffs[1]) == 0 else "imaginary"
            if kind in kinds:
                continue
            kinds.add(kind)
            miller = (pg.miller_value(self.B, A, self.COMPACT_LOOP)
                      * pg.miller_value(self.D, bn.neg(self.C), self.COMPACT_LOOP))
            denom = miller * self.numer / pg.fq12_pow(self.fold_root, self.compact_exponent)
            c = Circuit()
            moving = (pg.alloc_g2(c, self.B), pg.alloc_g1(c, A))
            fixed = (pg.const_g2(c, self.D), pg.alloc_g1(c, self.C).neg())
            pg.assert_pairing_compact(c, moving, fixed, self.numer, denom, self.COMPACT_LOOP,
                                      witness=(self.fold_root, 0), rescale=True)
            self.assertTrue(c.satisfied, (kind, c.failures[:3]))
            # The bundled check squares the line's constant term, which is plain or purely imaginary.
            for loop in (self.COMPACT_LOOP, self.OTHER_LOOP):
                c = self.bundled(style="wide", loop=loop, A=A)
                self.assertTrue(c.satisfied, (kind, c.failures[:3]))
        self.assertEqual(kinds, {"plain", "imaginary"})

    def test_no_bit_of_the_y_free_computation_is_left_free(self):
        c = self.compact(materialize=True, rescale=True)
        self.assertTrue(c.satisfied, c.failures[:3])
        self.assertTrue(holds(c.gates, c.x))
        self.assertEqual(bits_free_to_flip(c.gates, c.x), [])
        c = self.compact(self.wrong("rescaled: tangent", 1), materialize=True, rescale=True)
        self.assertFalse(c.satisfied)
        self.assertFalse(holds(c.gates, c.x))

    def test_the_compact_check_needs_no_choice(self):
        """Multiplying the right-hand constant by any power of omega changes
        nothing: omega lies in the subfield the classes ignore."""
        for style in STYLES:
            prints = set()
            for shift in (0, 1, 2, 5):
                c = self.compact(style=style, shift=shift)
                self.assertTrue(c.satisfied, c.failures[:3])
                prints.add(c.fingerprint())
            self.assertEqual(len(prints), 1)
        # A constant that is off by something outside the subfield is refused.
        pg = self.pg
        c = Circuit()
        moving, fixed = self.pairs(c)
        denom = (self.compact_miller * self.numer * pg.miller_value(self.bn.G2, self.bn.G1, "1")
                 / pg.fq12_pow(self.fold_root, self.compact_exponent))
        pg.assert_pairing_compact(c, moving, fixed, self.numer, denom, self.COMPACT_LOOP,
                                  witness=(self.fold_root, 0))
        self.assertEqual(set(c.failures), {"pairing equation"})

    def test_the_compact_check_wants_one_point_of_bits_and_one_constant(self):
        pg = self.pg
        c = Circuit()
        moving, fixed = self.pairs(c)
        for pair in ((moving, moving), (fixed, fixed), (fixed, moving)):
            with self.assertRaises(ValueError):
                pg.assert_pairing_compact(c, pair[0], pair[1], self.numer, self.denom, self.COMPACT_LOOP,
                                          witness=(self.fold_root, 0))

    def test_no_bit_of_a_compact_pairing_computation_is_left_free(self):
        c = self.compact(materialize=True)
        self.assertTrue(c.satisfied, c.failures[:3])
        self.assertGreater(c.n_bits, 200_000)
        self.assertTrue(holds(c.gates, c.x))
        self.assertEqual(bits_free_to_flip(c.gates, c.x), [])

    def test_the_folded_check_accepts_each_of_its_three_choices_and_only_the_right_one(self):
        pg = self.pg
        for style in STYLES:
            for choice in range(3):
                self.assertTrue(self.folded(choice, style=style).satisfied)
        # The right-hand constant was made for choice 2; claiming 0 or 1 must fail.
        omega2 = pg.fold_numbers()["powers"][2]
        denom = self.fold_miller * self.numer * omega2 / pg.fq12_pow(self.fold_root, self.fold_exponent)
        for claimed, expected in ((0, False), (1, False), (2, True)):
            c = Circuit()
            pg.assert_pairing_folded(c, self.pairs(c), self.numer, denom, "1",
                                     witness=(self.fold_root, claimed))
            self.assertEqual(c.satisfied, expected)

    def test_setting_both_choice_bits_is_refused_even_when_the_equation_would_hold(self):
        """The right-hand side of the last equation is picked by two bits. With
        both set it would be a fourth value that is none of the three allowed.
        Here the constants are arranged so that this fourth value is exactly
        what the left-hand side comes to, so the last equation holds and the
        only gate left to object is the one that forbids setting both bits."""
        pg, bn = self.pg, self.bn
        powers = pg.fold_numbers()["powers"]
        left = self.fold_miller / pg.fq12_pow(self.fold_root, self.fold_exponent)
        # left = denom/numer * (1/omega + 1/omega^2 - 1)  defines denom
        denom = left * self.numer / (powers[26] + powers[25] - bn.FQ12.one())
        for style in STYLES:
            c = Circuit(style=style)
            c.tamper = self.wrong("fold: one choice", 0)
            pg.assert_pairing_folded(c, self.pairs(c), self.numer, denom, "1",
                                     witness=(self.fold_root, 0))
            self.assertEqual(set(c.failures), {"fold: one choice"})

    def test_no_bit_of_a_whole_pairing_computation_is_left_free(self):
        c, _ = self.miller(materialize=True, root=True)
        self.assertTrue(c.satisfied, c.failures[:3])
        self.assertGreater(c.n_bits, 200_000)
        self.assertTrue(holds(c.gates, c.x))
        self.assertEqual(bits_free_to_flip(c.gates, c.x), [])

    def test_no_bit_of_a_folded_pairing_computation_is_left_free(self):
        c = self.folded(1, materialize=True)
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
        c = self.folded(1, self.wrong("fold: choice", 0), materialize=True)
        self.assertFalse(c.satisfied)
        self.assertFalse(holds(c.gates, c.x))
        c = self.compact(self.wrong("compact: lines", 3), materialize=True)
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
        self.assertEqual((c.style, c.max_products), ("wide", 3048))
        # Sizes, pinned so that changes to the encoding are noticed.
        self.assertTrue(540_000 < c.n_bits < 572_000, c.n_bits)
        self.assertTrue(3_790 < c.n_gates < 3_840, c.n_gates)

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

    def test_every_layout_and_final_check_agrees_and_has_its_expected_size(self):
        from py_ecc import bn128 as bn
        g, (A, B, C) = self.g, self.proof
        forged = (A, B, bn.multiply(bn.G1, 12345))
        expected = {                      # (style, check): bits, gates, most products in a gate
            ("wide", "rescaled"): ((610_000, 650_000), (4_280, 4_340), 3048),
            ("wide", "compact"): ((700_000, 745_000), (4_940, 5_000), 3048),
            ("wide", "folded"): ((1_400_000, 1_460_000), (9_900, 9_980), 3048),
            ("wide", "root"): ((2_480_000, 2_550_000), (17_300, 17_400), 3048),
            ("narrow", "rescaled"): ((2_950_000, 3_100_000), (18_200, 18_350), 99),
            ("narrow", "bundled"): ((3_300_000, 3_500_000), (20_200, 20_450), 483),
            ("narrow", "folded"): ((4_750_000, 4_850_000), (29_250, 29_450), 69),
            ("narrow", "root"): ((8_450_000, 8_580_000), (51_500, 51_750), 69),
        }
        sizes = [self.honest.n_bits]
        for (style, check), (bits, gates, widest) in expected.items():
            with self.subTest(style=style, check=check):
                c = g.encode_verifier(self.vk, [47053], self.proof, style=style, check=check)
                self.assertTrue(c.satisfied, c.failures[:3])
                self.assertTrue(bits[0] < c.n_bits < bits[1], c.n_bits)
                self.assertTrue(gates[0] < c.n_gates < gates[1], c.n_gates)
                self.assertEqual(c.max_products, widest)
                self.assertFalse(g.encode_verifier(self.vk, [47053], forged, style=style, check=check).satisfied)
                sizes.append(c.n_bits)
        self.assertEqual(sizes, sorted(sizes))     # each step back is larger
        with self.assertRaises(ValueError):
            g.encode_verifier(self.vk, [47053], self.proof, check="tiny")


if __name__ == "__main__":
    unittest.main()
