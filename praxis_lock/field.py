"""Arithmetic in the BN254 scalar field, and dense polynomials over it.

Written for clarity. Polynomial routines are quadratic, which is fine at toy
parameters and hopeless at the paper's (degree 3.8 million).
"""
from __future__ import annotations

import hashlib

# Order of BN254's groups, `Constants.q` in the Lean development.
Q = 21888242871839275222246405745257275088548364400416034343698204186575808495617


def inv(a: int) -> int:
    return pow(a, -1, Q)


def hash_to_field(*parts) -> int:
    """Derive a field element from byte strings and integers."""
    h = hashlib.sha512()
    for part in parts:
        data = part if isinstance(part, bytes) else str(part).encode()
        h.update(len(data).to_bytes(4, "big"))
        h.update(data)
    return int.from_bytes(h.digest(), "big") % Q


# ---------------------------------------------------------------- polynomials
# A polynomial is a list of coefficients, lowest degree first, with no
# trailing zeros. The zero polynomial is the empty list.

def trim(p):
    p = [c % Q for c in p]
    while p and p[-1] == 0:
        p.pop()
    return p


def padd(a, b):
    n = max(len(a), len(b))
    return trim([(a[i] if i < len(a) else 0) + (b[i] if i < len(b) else 0) for i in range(n)])


def psub(a, b):
    n = max(len(a), len(b))
    return trim([(a[i] if i < len(a) else 0) - (b[i] if i < len(b) else 0) for i in range(n)])


def pscale(c: int, a):
    return trim([c * x for x in a])


def pmul(a, b):
    if not a or not b:
        return []
    out = [0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b):
                out[i + j] = (out[i + j] + x * y) % Q
    return trim(out)


def peval(p, x: int) -> int:
    acc = 0
    for c in reversed(p):
        acc = (acc * x + c) % Q
    return acc


def pcoeff(p, k: int) -> int:
    return p[k] if 0 <= k < len(p) else 0


def pdivmod(a, b):
    """Quotient and remainder of a divided by b (b non-zero)."""
    a, b = trim(a), trim(b)
    if not b:
        raise ZeroDivisionError("division by the zero polynomial")
    quotient = [0] * max(len(a) - len(b) + 1, 0)
    lead_inv = inv(b[-1])
    rem = list(a)
    while len(rem) >= len(b):
        shift = len(rem) - len(b)
        factor = rem[-1] * lead_inv % Q
        quotient[shift] = factor
        for i, c in enumerate(b):
            rem[shift + i] = (rem[shift + i] - factor * c) % Q
        rem = trim(rem)
    return trim(quotient), rem


def poly_from_roots(roots):
    """The monic polynomial with exactly these roots."""
    p = [1]
    for r in roots:
        p = pmul(p, [(-r) % Q, 1])
    return p


def interpolate(xs, ys):
    """The polynomial of degree below len(xs) through the points (xs[k], ys[k])."""
    master = poly_from_roots(xs)
    out = []
    for xk, yk in zip(xs, ys):
        if yk % Q == 0:
            continue
        numer, rem = pdivmod(master, [(-xk) % Q, 1])
        assert not rem
        out = padd(out, pscale(yk * inv(peval(numer, xk)), numer))
    return out


def lagrange_basis_at(xs, t: int):
    """Values at t of the Lagrange basis polynomials for the nodes xs (t not a node)."""
    full = 1
    for x in xs:
        full = full * (t - x) % Q
    out = []
    for k, xk in enumerate(xs):
        denom = (t - xk) % Q
        for j, xj in enumerate(xs):
            if j != k:
                denom = denom * (xk - xj) % Q
        out.append(full * inv(denom) % Q)
    return out
