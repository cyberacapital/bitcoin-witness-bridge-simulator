"""Arithmetic modulo a foreign prime, inside the lock's gates.

The lock's equations hold modulo r, the BN254 group order. A proof verifier
works modulo p, the BN254 base field, a different 254-bit prime. So numbers
modulo p are written as bits, and one routine does all the work:

    assert_zero_mod(p, products, linears, constant)

asserts that   sum coef * A * B  +  sum coef * A  +  constant   is a multiple
of p, for integers A, B given as bits. In both layouts the prover supplies the
quotient k, so the claim becomes an equality of integers, total = k * p, and
that equality is checked in pieces small enough that holding modulo r means
holding over the integers.

Wide layout (`Circuit(style="wide")`, the default)
    Each number is one run of bits. A product A * B is expanded into its bits,
    a_i * b_j * 2^(i+j), and every constant coef * 2^(i+j) is reduced modulo p
    before anything is added up. All terms are then non-negative and below p,
    so the total is at most (number of terms) * p and the quotient is as small
    as a count of terms: about 20 bits, not 254. The constants are cut into a
    low and a high half, giving two gates joined by one carry. A gate's
    products are single bits of A times long sums over the bits of B. One
    check costs two gates and about 40 bookkeeping bits.

Narrow layout (`Circuit(style="narrow")`)
    Numbers are cut into limbs of 85 bits and multiplied limb by limb, with a
    full-size quotient and a carry between each limb position and the next.
    One check costs five gates of at most a few products each, and several
    hundred bookkeeping bits.

Soundness depends on the bounds computed here: every bit run can hold any value
of its width, so ranges are taken from widths, never from honest values.

Nothing in this file decides whether an assignment passes. It fills in the
quotient and the carries by rounding down and records the gates; if the claim
is false, some gate fails when the builder evaluates it.
"""
from __future__ import annotations

import array
import sys
from collections import defaultdict
from operator import mul

from ..field import Q as R_MOD
from ..relation import Gate
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
        w = remaining if c.style == "wide" else min(c.limb_bits, remaining)
        limbs.append(Limb(c.alloc((v >> shift) & ((1 << w) - 1), w)))
        shift += w
        remaining -= w
    return Big(limbs, v)


def const_big(c: Circuit, value: int) -> Big:
    """A non-negative constant, split into limbs. Costs no bits."""
    if value < 0:
        raise ValueError("constants must be non-negative; use a negative coefficient")
    if c.style == "wide":
        return Big([Limb(value=value)], value)
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
    if c.style == "wide":
        _assert_zero_mod_wide(c, p, products, linears, constant, label)
    else:
        _assert_zero_mod_narrow(c, p, products, linears, constant, label)


# ---------------------------------------------------------------- wide layout

_DIGIT = "I" if array.array("I").itemsize == 4 else "L"
if array.array(_DIGIT).itemsize != 4:                      # pragma: no cover
    raise ImportError("no 32-bit array type on this platform")

_DIGIT_ROOM = 1 << 32            # a count of bit pairs must stay below this to fit its digit
_TABLES = {}
_ONES = {}


def _digits(value: int, n: int):
    """The n lowest 32-bit digits of a non-negative integer."""
    out = array.array(_DIGIT)
    out.frombytes(value.to_bytes(4 * n, "little"))
    if sys.byteorder == "big":                             # pragma: no cover
        out.byteswap()
    return out


def _pack_shift(lam: int) -> int:
    """Where the high half sits when both halves share one number. A sum of low
    halves is below 2^lam times a count below 2^32 times a length below 2^32."""
    return lam + 64


def _tables(p: int, k: int, n: int, lam: int):
    """K * 2^s mod p for s < n, as three lists: the low `lam` bits, the rest,
    and both packed into one number, for summing in one pass."""
    key = (p, k, lam)
    cached = _TABLES.get(key)
    if cached is not None and len(cached[0]) >= n:
        return cached
    low, high, both, mask, t, shift = [], [], [], (1 << lam) - 1, k, _pack_shift(lam)
    for _ in range(n):
        low.append(t & mask)
        high.append(t >> lam)
        both.append((t & mask) | ((t >> lam) << shift))
        t <<= 1
        if t >= p:
            t -= p
    if min(k, p - k) < 1 << 24:          # the handful of small coefficients; keep them
        _TABLES[key] = (low, high, both)
    return low, high, both


def _weigh(counts, both, lam: int):
    """sum of counts[s] * table[s], for the low and the high halves at once."""
    total, shift = sum(map(mul, counts, both)), _pack_shift(lam)
    return total & ((1 << shift) - 1), total >> shift


def _ones(p: int, k: int, lam: int, wa: int, wb: int):
    """What a product term adds to each half when every bit of both numbers is 1."""
    key = (p, k, lam, wa, wb)
    cached = _ONES.get(key)
    if cached is None:
        low, high, _ = _tables(p, k, wa + wb - 1, lam)
        counts = [min(s, wa - 1, wb - 1, wa + wb - 2 - s) + 1 for s in range(wa + wb - 1)]
        cached = (sum(map(mul, counts, low)), sum(map(mul, counts, high)))
        if min(k, p - k) < 1 << 24:
            _ONES[key] = cached
    return cached


def _run(big: Big) -> Rng:
    if len(big.limbs) != 1 or big.limbs[0].rng is None:
        raise ValueError("the wide layout needs each number as one run of bits")
    return big.limbs[0].rng


def _assert_zero_mod_wide(c: Circuit, p: int, products, linears, constant: int, label: str) -> None:
    lam = (p.bit_length() + 1) // 2
    beta = 1 << lam
    p_low, p_high = p & (beta - 1), p >> lam

    # Sort the claim into bit-by-bit products, bit-by-constant terms, and a constant.
    pairs, lins, const = {}, {}, constant

    def add_lin(k, big):
        run = _run(big)
        entry = lins.get(run.start)
        if entry:
            entry[0] += k
        else:
            lins[run.start] = [k, run]

    for coef, a, b in products:
        if a.is_const and b.is_const:
            const += coef * a.value * b.value
        elif a.is_const:
            add_lin(coef * a.value, b)
        elif b.is_const:
            add_lin(coef * b.value, a)
        else:
            ra, rb = _run(a), _run(b)
            if (ra.start, rb.start) not in pairs and (rb.start, ra.start) in pairs:
                ra, rb = rb, ra
            entry = pairs.get((ra.start, rb.start))
            if entry:
                entry[0] += coef
            else:
                pairs[(ra.start, rb.start)] = [coef, ra, rb]
    for coef, a in linears:
        if a.is_const:
            const += coef * a.value
        else:
            add_lin(coef, a)

    pair_list = [(k % p, ra, rb) for k, ra, rb in pairs.values() if k % p]
    # Whichever side has fewer bits in all is expanded bit by bit: fewer products.
    firsts = {ra.start: ra.width for _, ra, _ in pair_list}
    seconds = {rb.start: rb.width for _, _, rb in pair_list}
    if sum(seconds.values()) < sum(firsts.values()):
        pair_list, firsts = [(k, rb, ra) for k, ra, rb in pair_list], seconds
    pair_list.sort(key=lambda t: (t[1].start, t[2].start))
    lin_list = sorted(((k % p, run) for k, run in lins.values() if k % p), key=lambda t: t[1].start)
    tc = const % p
    tc_half = (tc & (beta - 1), tc >> lam)

    # The honest value of each half of the total, and the most it could be.
    prod_val, lin_val, top = [0, 0], [0, 0], [0, 0]
    by_k = {}
    for k, ra, rb in pair_list:
        by_k.setdefault(k, []).append((ra, rb))
        most = _ones(p, k, lam, ra.width, rb.width)
        top[0] += most[0]
        top[1] += most[1]
    for k, items in by_k.items():
        n = max(ra.width + rb.width - 1 for ra, rb in items)
        if sum(min(ra.width, rb.width) for ra, rb in items) >= _DIGIT_ROOM:
            raise OverflowError("too many products share one coefficient")
        counts = _digits(sum(c.bit_pairs(ra, rb) for ra, rb in items), n)
        part = _weigh(counts, _tables(p, k, n, lam)[2], lam)
        prod_val[0] += part[0]
        prod_val[1] += part[1]
    for k, run in lin_list:
        low, high, both = _tables(p, k, run.width, lam)
        part = _weigh(_digits(run.spread(), run.width), both, lam)
        lin_val[0] += part[0]
        lin_val[1] += part[1]
        top[0] += sum(low[:run.width])
        top[1] += sum(high[:run.width])

    # The quotient: every term is non-negative, so it runs from 0 to (most possible) / p.
    total = (prod_val[0] + lin_val[0] + tc_half[0]) + beta * (prod_val[1] + lin_val[1] + tc_half[1])
    k_most = ((top[0] + tc_half[0]) + beta * (top[1] + tc_half[1])) // p
    k_run = c.alloc(total // p, max(1, k_most.bit_length()))
    k_cap = (1 << k_run.width) - 1                         # what its bits can hold

    # The carry from the low half to the high half, stored with an offset so it is non-negative.
    offset = -((-k_cap * p_low) // beta)
    carry_most = (top[0] + tc_half[0]) // beta
    carry_val = (prod_val[0] + lin_val[0] + tc_half[0] - k_run.value * p_low) // beta
    carry_run = c.alloc(carry_val + offset, max(1, (offset + carry_most).bit_length()))
    carry_cap = (1 << carry_run.width) - 1

    # Both gates must stay far below r whatever the bits hold, so that "equal modulo r" means "equal".
    low_span = max(k_cap * p_low + beta * (carry_cap - offset), top[0] + tc_half[0] + beta * offset)
    high_span = max(k_cap * p_high + offset, top[1] + tc_half[1] + carry_cap)
    if max(low_span, high_span) >= R_MOD // 2:
        raise OverflowError("gate too large for the lock's field")

    n_products = sum(firsts.values())
    carry = carry_run.value - offset
    shape = (p, lam, [(k, ra.start, ra.width, rb.start, rb.width) for k, ra, rb in pair_list],
             [(k, run.start, run.width) for k, run in lin_list], tc,
             k_run.start, k_run.width, carry_run.start, carry_run.width, offset)
    for half in (0, 1):
        # low half:   products + linear + constant - k * p_low  = beta * carry
        # high half:  products + linear + constant - k * p_high = -carry
        p_half = (p_low, p_high)[half]
        carry_coef = (beta, -1)[half]
        c.record(
            lhs=prod_val[half],
            rhs=-lin_val[half] - tc_half[half] + k_run.value * p_half + carry_coef * carry,
            n_products=n_products, shape=("wide", half) + shape, label=label,
            build=lambda half=half, p_half=p_half, carry_coef=carry_coef: _wide_gate(
                p, lam, half, pair_list, lin_list, tc_half[half], k_run, p_half,
                carry_run, carry_coef, offset))


def _wide_gate(p, lam, half, pair_list, lin_list, tc, k_run, p_half, carry_run, carry_coef, offset) -> Gate:
    """One half of a wide check, written out in the lock's own format."""
    by_left = {}
    for k, ra, rb in pair_list:
        by_left.setdefault(ra.start, (ra, []))[1].append((k, rb))
    left, right = [], []
    for ra, partners in by_left.values():
        for i in range(ra.width):
            form = {}
            for k, rb in partners:             # each partner is a different run of bits
                table = _tables(p, k, ra.width + rb.width - 1, lam)[half]
                for j in range(rb.width):
                    if table[i + j]:
                        form[rb.start + j] = table[i + j]
            left.append({ra.start + i: 1})
            right.append(form)
    linear = {0: (-tc - carry_coef * offset) % R_MOD}
    for k, run in lin_list:
        table = _tables(p, k, run.width, lam)[half]
        for j in range(run.width):
            linear[run.start + j] = (linear.get(run.start + j, 0) - table[j]) % R_MOD
    for t in range(k_run.width):
        linear[k_run.start + t] = (p_half << t) % R_MOD
    for t in range(carry_run.width):
        linear[carry_run.start + t] = (carry_coef << t) % R_MOD
    return Gate(left=tuple(left), right=tuple(right), linear=linear)


# ---------------------------------------------------------------- narrow layout

def _assert_zero_mod_narrow(c: Circuit, p: int, products, linears, constant: int, label: str) -> None:
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
