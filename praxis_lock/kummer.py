"""The extension-field side of the scheme: forms, weights, and the grid of cells.

Praxis/Forms.lean, Praxis/Kummer.lean, Praxis/Grid.lean.

A seed expands into n linear forms v_0 .. v_{n-1}. Applied to a solution x they
give z = sum_e <v_e, x> X^e, an element of K = F_q[X] / (X^n - c0); this is a
Module-SIS hash of x. Two products in K supply the weights that tie the copies
of the hidden matrix together:

    U = z * sigma^{m-1}(z)        V = z * sigma^m(z)

where sigma^s sends X to beta^s X.
"""
from __future__ import annotations

import hashlib

from .field import Q
from .params import Params

LEG_KINDS = ("E", "O", "V")
LEG_BASE = {"E": 0, "O": 1, "V": 0}


# ---------------------------------------------------------------- grid of cells

def legs(P: Params):
    """`legList`: every (kind, k2)."""
    return [(kind, k2) for kind in LEG_KINDS for k2 in range(P.K2)]


def cells(P: Params):
    """`cellList`: every (k1, leg)."""
    return [(k1, leg) for k1 in range(P.K1) for leg in legs(P)]


def leg_exp(P: Params, leg) -> int:
    return LEG_BASE[leg[0]] + 2 * P.K1 * leg[1]


def cell_exp(P: Params, cell) -> int:
    return 2 * cell[0] + leg_exp(P, cell[1])


def is_u_cell(cell) -> bool:
    return cell[1][0] != "V"


def is_key_cell(cell) -> bool:
    return cell == (0, ("E", 0))


KEY_CELL = (0, ("E", 0))


# ---------------------------------------------------------------- forms from a seed

def form_matrix(P: Params, seed: bytes):
    """The random oracle H_seed: `rows` x (1 + cols) elements of F_q[X]/(X^d + 1).

    Entry [rho][0] is the constant column a*, entry [rho][1 + j] is column j.
    """
    def element(rho, col):
        out = []
        for s in range(P.d):
            h = hashlib.sha512(b"praxis-lock/forms" + seed + bytes([0]) +
                               f"{rho}/{col}/{s}".encode()).digest()
            out.append(int.from_bytes(h, "big") % Q)
        return out
    return [[element(rho, col) for col in range(P.cols + 1)] for rho in range(P.rows)]


def rq_mul(P: Params, a, b):
    """Multiplication in F_q[X] / (X^d + 1)."""
    out = [0] * P.d
    for j, aj in enumerate(a):
        if aj:
            for k, bk in enumerate(b):
                if j + k < P.d:
                    out[j + k] = (out[j + k] + aj * bk) % Q
                else:
                    out[j + k - P.d] = (out[j + k - P.d] - aj * bk) % Q
    return out


def slot_poly(P: Params, x, j: int):
    return [x[1 + P.d * j + s] if 1 + P.d * j + s < P.N + 1 else 0 for s in range(P.d)]


def phi(P: Params, A, e: int, x) -> int:
    """Coefficient e of the hash of x: (x_0, a* x_0 + A x) packed d at a time."""
    block, s = divmod(e, P.d)
    if block == 0:
        return x[0] % Q if s == 0 else 0
    row = A[block - 1]
    total = x[0] * row[0][s]
    for j in range(P.cols):
        total += rq_mul(P, row[1 + j], slot_poly(P, x, j))[s]
    return total % Q


def form_vectors(P: Params, A):
    """`formVec`: v[e][c] is the coefficient of x_c in coefficient e of the hash."""
    units = [[1 if i == c else 0 for i in range(P.N + 1)] for c in range(P.N + 1)]
    return [[phi(P, A, e, unit) for unit in units] for e in range(P.n)]


def forms_of(P: Params, seed: bytes):
    return form_vectors(P, form_matrix(P, seed))


# ---------------------------------------------------------------- arithmetic in K

def z_of(P: Params, v, x):
    return [sum(c * xi for c, xi in zip(row, x)) % Q for row in v]


def sigma(P: Params, s: int, a):
    """`frob`: scale coefficient e by beta^(s e). Works on scalars."""
    return [pow(P.beta, s * e, Q) * c % Q for e, c in enumerate(a)]


def kmul(P: Params, a, b):
    """Multiplication in K = F_q[X] / (X^n - c0)."""
    raw = [0] * (2 * P.n)
    for i, ai in enumerate(a):
        if ai:
            for j, bj in enumerate(b):
                raw[i + j] = (raw[i + j] + ai * bj) % Q
    return [(raw[e] + P.c0 * raw[e + P.n]) % Q for e in range(P.n)]


def weight_products(P: Params, z):
    """The two products whose coefficients are the weights: (U, V)."""
    return kmul(P, z, sigma(P, P.m - 1, z)), kmul(P, z, sigma(P, P.m, z))


def weight(P: Params, prod_u, prod_v, cell) -> int:
    """`weight`: Uw(cellExp) on E and O legs, Vw(cellExp / 2) on V legs."""
    e = cell_exp(P, cell)
    return prod_u[e] if is_u_cell(cell) else prod_v[2 * (e // 2)]


def lam(P: Params, v, s: int):
    """`lam`: sum_e s^e v_e, the forms evaluated at X = s."""
    out = [0] * (P.N + 1)
    power = 1
    for row in v:
        for c, coeff in enumerate(row):
            out[c] = (out[c] + power * coeff) % Q
        power = power * s % Q
    return out
