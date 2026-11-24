"""Arithmetic modulo a foreign prime, inside the lock's gates.

The lock's equations hold modulo r, the BN254 group order. A proof verifier
works modulo p, the BN254 base field, a different 254-bit prime. So numbers
modulo p are written as bits, grouped into limbs of 85 bits, and one routine
does all the work:

    assert_zero_mod(p, products, linears, constant)

asserts that   sum coef * A * B  +  sum coef * A  +  constant   is a multiple
of p, for integers A, B given as limbs.

How: the prover supplies the quotient k, so the claim becomes an equality of
integers, total = k * p. That equality is checked limb position by limb
position, with a carry handed from each position to the next. Every per-position
equation involves only numbers far smaller than r, so holding modulo r means
holding over the integers, and the positions together give the full equality.

Soundness depends on the bounds computed here: every bit run can hold any value
of its width, so ranges are taken from widths, never from honest values.

Nothing in this file decides whether an assignment passes. It fills in the
quotient and the carries by rounding down and records the gates; if the claim
is false, some gate fails when the builder evaluates it.
"""
from __future__ import annotations

from collections import defaultdict

from ..field import Q as R_MOD
from .circuit import Circuit, Rng


class Limb:
    """One limb: either a run of bits or a constant."""
    __slots__ = ("rng", "value", "hi")

    def __init__(self, rng: Rng = None, value: int = 0):
        self.rng = rng
        self.value = rng.value if rng is not None else value
        self.hi = (1 << rng.width) - 1 if rng is not None else value


class Big:
    """A non-negative integer as limbs, lowest first, in base 2^limb_bits."""
    __slots__ = ("limbs", "value")

    def __init__(self, limbs, value: int):
        self.limbs, self.value = limbs, value

    @property
    def is_const(self) -> bool:
        return all(l.rng is None for l in self.limbs)


def alloc_big(c: Circuit, value: int, bits: int) -> Big:
    """Fresh bits for an integer below 2^bits."""
    limbs, remaining, v = [], bits, max(value, 0)
    if value < 0 or value >> bits:
        c.satisfied = False
        c.failures.append("a value does not fit its bits")
        v &= (1 << bits) - 1
    shift = 0
    while remaining > 0:
        w = min(c.limb_bits, remaining)
        limbs.append(Limb(c.alloc((v >> shift) & ((1 << w) - 1), w)))
        shift += w
        remaining -= w
    return Big(limbs, v)


def const_big(c: Circuit, value: int) -> Big:
    """A non-negative constant, split into limbs. Costs no bits."""
    if value < 0:
        raise ValueError("constants must be non-negative; use a negative coefficient")
    base, limbs, v = 1 << c.limb_bits, [], value
    while True:
        limbs.append(Limb(value=v % base))
        v //= base
        if v == 0:
            break
    return Big(limbs, value)


def assert_zero_mod(c: Circuit, p: int, products=(), linears=(), constant: int = 0,
                    label: str = "") -> None:
    """Assert  sum coef*A*B + sum coef*A + constant  is divisible by p.

    products: (coef, A, B) with integer coef and Big A, B.
    linears:  (coef, A).
    """
    base = 1 << c.limb_bits
    prods = defaultdict(list)   # position -> (coef, Rng, Rng)
    lins = defaultdict(list)    # position -> (coef, Rng)
    lo = defaultdict(int)       # least value the variable part of a position can take
    hi = defaultdict(int)       # greatest
    val = defaultdict(int)      # its honest value
    const_total = constant

    def add_lin(i, coef, limb):
        nonlocal const_total
        if coef == 0:
            return
        if limb.rng is None:
            const_total += coef * limb.value * base**i
            return
        lins[i].append((coef, limb.rng))
        val[i] += coef * limb.value
        if coef > 0:
            hi[i] += coef * limb.hi
        else:
            lo[i] += coef * limb.hi

    def add_prod(i, coef, la, lb):
        nonlocal const_total
        if coef == 0:
            return
        if la.rng is None:
            add_lin(i, coef * la.value, lb)
        elif lb.rng is None:
            add_lin(i, coef * lb.value, la)
        else:
            prods[i].append((coef, la.rng, lb.rng))
            val[i] += coef * la.value * lb.value
            if coef > 0:
                hi[i] += coef * la.hi * lb.hi
            else:
                lo[i] += coef * la.hi * lb.hi

    for coef, a, b in products:
        for j, la in enumerate(a.limbs):
            for l, lb in enumerate(b.limbs):
                add_prod(j + l, coef, la, lb)
    for coef, a in linears:
        for j, la in enumerate(a.limbs):
            add_lin(j, coef, la)

    top = max([0, *prods, *lins])
    total = const_total + sum(val[i] * base**i for i in range(top + 1))
    total_lo = const_total + sum(lo[i] * base**i for i in range(top + 1))
    total_hi = const_total + sum(hi[i] * base**i for i in range(top + 1))

    # The quotient k = total / p lies in [k_lo, k_hi]; store k - k_lo in fresh bits.
    k_lo, k_hi = total_lo // p, -((-total_hi) // p)
    kb = alloc_big(c, total // p - k_lo, max(1, (k_hi - k_lo).bit_length()))
    p_limbs = const_big(c, p).limbs
    for j, lk in enumerate(kb.limbs):
        for l, lp in enumerate(p_limbs):
            add_lin(j + l, -lp.value, lk)          # minus (k - k_lo) * p
    const_total -= k_lo * p                         # minus k_lo * p

    last = max([0, *prods, *lins])
    digits, rest = [], const_total
    for _ in range(last):
        digits.append(rest % base)
        rest //= base
    digits.append(rest)

    carry_rng, carry_lo, carry_hi, carry_val = None, 0, 0, 0
    for i in range(last + 1):
        s_val = val[i] + digits[i] + carry_val
        s_lo = lo[i] + digits[i] + carry_lo
        s_hi = hi[i] + digits[i] + carry_hi
        const = -digits[i]
        linear = [(-coef, rng) for coef, rng in lins[i]]
        if carry_rng is not None:
            linear.append((-1, carry_rng))
            const -= carry_lo
        if i < last:
            # s = base * carry, with the carry stored as an offset in fresh bits.
            n_lo, n_hi = s_lo // base, -((-s_hi) // base)
            width = max(1, (n_hi - n_lo).bit_length())
            new_rng = c.alloc(s_val // base - n_lo, width)
            n_hi = n_lo + (1 << width) - 1          # what the bits can actually hold
            if (s_hi - s_lo) + base * (n_hi - n_lo) >= R_MOD // 2:
                raise OverflowError("limb equation too large for the lock's field")
            linear.append((base, new_rng))
            const += base * n_lo
            c.gate(prods[i], const, linear, label)
            carry_rng, carry_lo, carry_hi, carry_val = new_rng, n_lo, n_hi, s_val // base
        else:
            if max(abs(s_lo), abs(s_hi)) >= R_MOD // 2:
                raise OverflowError("limb equation too large for the lock's field")
            c.gate(prods[i], const, linear, label)
