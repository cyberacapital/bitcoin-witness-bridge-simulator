"""Elements of F_p, F_p^2 and F_p^12 as bits, and identities between them.

An element of F_p is a `Lin`: a small-integer combination of limbed integers
(`Big`). Fresh elements are single 254-bit integers; sums, differences and
small multiples cost nothing, they are just longer combinations.

F_p^2 = F_p[u] / (u^2 + 1) is a pair of `Lin`. F_p^12 is twelve, in the basis
1, w, ..., w^11 with w^12 = 18 w^6 - 82, the same representation the py_ecc
library uses, so values can be checked against it directly.

The three `*_assert` functions each state one identity of degree at most two:

    sum coef * A * B  +  sum coef * A  =  0

and expand it into calls of `assert_zero_mod`.
"""
from __future__ import annotations

from .circuit import Circuit
from .emulated import Big, alloc_big, assert_zero_mod, const_big

# The BN254 base field.
P = 21888242871839275222246405745257275088696311157297823662689037894645226208583
FP_BITS = 254


class Lin:
    """sum of coef * Big, an element of F_p (its value is taken modulo p)."""
    __slots__ = ("terms",)

    def __init__(self, terms=()):
        self.terms = [(k, b) for k, b in terms if k]

    def __bool__(self):
        return bool(self.terms)

    def __add__(self, other):
        return Lin(self.terms + other.terms)

    def __sub__(self, other):
        return Lin(self.terms + [(-k, b) for k, b in other.terms])

    def __neg__(self):
        return Lin([(-k, b) for k, b in self.terms])

    def scale(self, k: int):
        return Lin([(k * c, b) for c, b in self.terms])

    def value(self, p: int = P) -> int:
        return sum(k * b.value for k, b in self.terms) % p


ZERO = Lin()


def fp_alloc(c: Circuit, value: int, p: int = P, bits: int = FP_BITS) -> Lin:
    """Fresh bits for an element of F_p."""
    return Lin([(1, alloc_big(c, value % p, bits))])


def fp_const(c: Circuit, value: int, p: int = P) -> Lin:
    value %= p
    return Lin([(1, const_big(c, value))]) if value else ZERO


def fp_assert(c: Circuit, products=(), linears=(), p: int = P, label: str = "") -> None:
    """Assert  sum coef * a * b + sum coef * a = 0  in F_p, for `Lin` a, b."""
    prod_terms, lin_terms = {}, {}
    for coef, a, b in products:
        for ka, ba in a.terms:
            for kb, bb in b.terms:
                key = (id(ba), id(bb))
                entry = prod_terms.get(key)
                prod_terms[key] = (entry[0] + coef * ka * kb if entry else coef * ka * kb, ba, bb)
    for coef, a in linears:
        for ka, ba in a.terms:
            entry = lin_terms.get(id(ba))
            lin_terms[id(ba)] = (entry[0] + coef * ka if entry else coef * ka, ba)
    products_ = [t for t in prod_terms.values() if t[0]]
    linears_ = [t for t in lin_terms.values() if t[0]]
    if not products_ and not linears_:
        return
    assert_zero_mod(c, p, products_, linears_, 0, label)


# ---------------------------------------------------------------- F_p^2

def fq2_alloc(c: Circuit, value):
    return (fp_alloc(c, value[0]), fp_alloc(c, value[1]))


def fq2_const(c: Circuit, value):
    return (fp_const(c, value[0]), fp_const(c, value[1]))


def fq2_value(a):
    return (a[0].value(), a[1].value())


def fq2_assert(c: Circuit, products=(), linears=(), label: str = "") -> None:
    """One identity in F_p^2, where u^2 = -1."""
    real = [(k, a[0], b[0]) for k, a, b in products] + [(-k, a[1], b[1]) for k, a, b in products]
    imag = [(k, a[0], b[1]) for k, a, b in products] + [(k, a[1], b[0]) for k, a, b in products]
    fp_assert(c, real, [(k, a[0]) for k, a in linears], label=label)
    fp_assert(c, imag, [(k, a[1]) for k, a in linears], label=label)


# ---------------------------------------------------------------- F_p^12

def _reduction_table():
    """w^t for t up to 22 in the basis 1..w^11, using w^12 = 18 w^6 - 82."""
    table = []
    for t in range(23):
        if t < 12:
            table.append({t: 1})
        else:
            out = {}
            for k, v in table[t - 6].items():
                out[k] = out.get(k, 0) + 18 * v
            for k, v in table[t - 12].items():
                out[k] = out.get(k, 0) - 82 * v
            table.append({k: v for k, v in out.items() if v})
    return table


RED = _reduction_table()


def fq12_alloc(c: Circuit, value):
    return [fp_alloc(c, v) for v in value]


def fq12_const(c: Circuit, value):
    return [fp_const(c, v) for v in value]


def fq12_value(a):
    return [x.value() for x in a]


def fq12_assert(c: Circuit, products=(), linears=(), label: str = "") -> None:
    """One identity in F_p^12: twelve identities in F_p, one per coefficient."""
    for k in range(12):
        prods = []
        for coef, a, b in products:
            for i, ai in enumerate(a):
                if not ai:
                    continue
                for j, bj in enumerate(b):
                    rc = RED[i + j].get(k)
                    if bj and rc:
                        prods.append((coef * rc, ai, bj))
        fp_assert(c, prods, [(coef, a[k]) for coef, a in linears if a[k]], label=label)


def embed_fq2(a):
    """F_p^2 inside F_p^12: a0 + a1 u  goes to  (a0 - 9 a1) + a1 w^6."""
    out = [ZERO] * 12
    out[0] = a[0] - a[1].scale(9)
    out[6] = a[1]
    return out
