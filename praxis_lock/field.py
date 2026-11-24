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
    if len(a) < len(b):
        return [], a
    rem = list(a)
    lead_inv = inv(b[-1])
    quotient = [0] * (len(a) - len(b) + 1)
    for shift in range(len(a) - len(b), -1, -1):
        factor = rem[shift + len(b) - 1] * lead_inv % Q
        if factor:
            quotient[shift] = factor
            for i, c in enumerate(b):
                rem[shift + i] = (rem[shift + i] - factor * c) % Q
    return trim(quotient), trim(rem[:len(b) - 1])


def poly_from_roots(roots):
    """The monic polynomial with exactly these roots."""
    p = [1]
    for r in roots:
        p = pmul(p, [(-r) % Q, 1])
    return p


def _divide_by_root(master, root: int):
    """master / (X - root) by synthetic division, for a root of master."""
    out = [0] * (len(master) - 1)
    carry = 0
    for i in range(len(master) - 1, 0, -1):
        carry = (master[i] + carry * root) % Q
        out[i - 1] = carry
    return out


def interpolate(xs, ys):
    """The polynomial of degree below len(xs) through the points (xs[k], ys[k])."""
    master = poly_from_roots(xs)
    out = [0] * len(xs)
    for xk, yk in zip(xs, ys):
        if yk % Q == 0:
            continue
        numer = _divide_by_root(master, xk)
        scale = yk * inv(peval(numer, xk)) % Q
        for i, c in enumerate(numer):
            out[i] = (out[i] + scale * c) % Q
    return trim(out)


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
