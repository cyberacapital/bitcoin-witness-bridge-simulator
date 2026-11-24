"""The decryption formulas exactly as the Lean text writes them, with no shortcuts.

`evalComb`, `stackComb`-style coefficients, `tiePoly`, `tieQuot`, `crossPoly`,
`honestStack` and `psi` from Praxis/Dec.lean and Praxis/Stack.lean, as literal
sums over full matrices. Slow, and only used by the tests, to check that the
collapsed formulas in `scheme.py` compute the same thing.
"""
from __future__ import annotations

from .field import Q, pcoeff
from .kummer import cell_exp, cells, is_key_cell, is_u_cell, leg_exp, weight, weight_products, z_of
from .params import Params
from .scheme import CROSS, TIES


def _mat_vec(M, b):
    return [sum(mij * bj for mij, bj in zip(row, b)) % Q for row in M]


def _vec_add(a, b, sign=1):
    return [(p + sign * q) % Q for p, q in zip(a, b)]


def _zero_mat(P):
    return [[0] * (P.N + 1) for _ in range(P.N + 1)]


def honest_stack(P: Params, w, x):
    """`honestStack`: in every cell, weight times x x^T."""
    return {cell: [[w[cell] * xi * xj % Q for xj in x] for xi in x] for cell in cells(P)}


def poly_u(P: Params, W):
    out = [_zero_mat(P) for _ in range(P.n)]
    for cell, M in W.items():
        if is_u_cell(cell):
            k = cell_exp(P, cell)
            out[k] = [[(a + b) % Q for a, b in zip(r1, r2)] for r1, r2 in zip(out[k], M)]
    return out


def poly_v(P: Params, W):
    out = [_zero_mat(P) for _ in range(P.n)]
    for cell, M in W.items():
        if not is_u_cell(cell):
            k = cell_exp(P, cell)  # always even on V legs
            out[k] = [[(a + b) % Q for a, b in zip(r1, r2)] for r1, r2 in zip(out[k], M)]
    return out


def _sigma_vecs(P, s, vecs):
    return [[pow(P.beta, s * e, Q) * c % Q for c in vec] for e, vec in enumerate(vecs)]


def _sigma_mats(P, s, mats):
    return [[[pow(P.beta, s * e, Q) * c % Q for c in row] for row in M] for e, M in enumerate(mats)]


def raw_mul_mv(P: Params, mats, vecs):
    """`rawMulMV`: coefficient t is the sum over i + j = t of mats[i] * vecs[j]."""
    out = [[0] * (P.N + 1) for _ in range(2 * P.n - 1)]
    for i, M in enumerate(mats):
        if not any(any(row) for row in M):
            continue
        for j, b in enumerate(vecs):
            out[i + j] = _vec_add(out[i + j], _mat_vec(M, b))
    return out


def tie_poly(P: Params, v, W):
    """`tiePoly`: the three identities that tie the copies of the matrix together."""
    m = P.m
    pu, pv = poly_u(P, W), poly_v(P, W)
    spu = _sigma_mats(P, m + 1, pu)
    sv = {s: _sigma_vecs(P, s, v) for s in (m - 1, m, m + 1)}

    def diff(a, b, c, d):
        return [_vec_add(p, q, -1) for p, q in zip(raw_mul_mv(P, a, b), raw_mul_mv(P, c, d))]

    return {"t1": diff(pu, sv[m], pv, sv[m - 1]),
            "t2": diff(pu, sv[m + 1], spu, sv[m - 1]),
            "t3": diff(pv, sv[m + 1], spu, sv[m])}


def ties_hold(P: Params, v, W) -> bool:
    """`Ties`: every tie polynomial vanishes modulo X^n - c0."""
    for poly in tie_poly(P, v, W).values():
        for e in range(P.n):
            high = poly[e + P.n] if e + P.n < len(poly) else [0] * (P.N + 1)
            if any((a + P.c0 * b) % Q for a, b in zip(poly[e], high)):
                return False
    return True


def tie_quot(P: Params, v, W):
    """`tieQuot`: the part of each tie polynomial above degree n."""
    return {t: [poly[e + P.n] for e in range(P.n - 1)] for t, poly in tie_poly(P, v, W).items()}


def cross_poly(P: Params, v, W):
    """`crossPoly`, built from `shiftLam`."""
    beta, top = P.beta, pow(P.beta, P.m - 1, Q)
    out = {g: [[0] * (P.N + 1) for _ in range(2 * P.n - 1)] for g in CROSS}

    def add(g, e, scale, vec):
        out[g][e] = [(a + scale * b) % Q for a, b in zip(out[g][e], vec)]

    for cell, M in W.items():
        k = cell_exp(P, cell)
        theta = pow(beta, 2 * cell[0], Q)
        beta_leg = pow(-beta % Q, leg_exp(P, cell[1]), Q)
        for idx in range(P.n):
            mv = _mat_vec(M, v[idx])
            e = k + idx
            minus_one, neg_beta, top_pow = pow(Q - 1, idx, Q), pow(-beta % Q, idx, Q), pow(top, idx, Q)
            if is_u_cell(cell):
                add("c1eps", e, theta * minus_one, mv)
                add("c2eps", e, theta * neg_beta, mv)
                add("c2div", e, -beta_leg * top_pow, mv)
                add("c3div", e, -beta_leg * minus_one, mv)
            else:
                add("c1eps", e, -theta * top_pow, mv)
                add("c3eps", e, theta * neg_beta, mv)
    return out


def eval_comb(G, P: Params, ct, sector: str, W, sc, h, R, Qv, Xv, key: int):
    """`evalComb`, as the literal sums over every index."""
    g1, coords = ct.g1, range(P.N + 1)
    g2 = lambda kind, *rest: ct.g2[(kind, sector) + rest]
    pos, neg = [], []
    for cell in cells(P):
        k1, leg = cell
        for i in coords:
            for j in coords:
                c = W[cell][i][j]
                if c:
                    pos.append((G.mul1(c, g1[("L1", k1, i)]), g2("ZC", leg, j)))
                    pos.append((G.mul1(c, g1[("L2", k1, i)]), g2("R2", leg, j)))
            if sc[cell][i]:
                neg.append((G.mul1(sc[cell][i], g1[("L1", k1, i)]), g2("SC", leg)))
        for a in range(P.E1):
            for j in range(P.Mq):
                c = pcoeff(h[cell], a + P.E1 * j)
                if c:
                    neg.append((G.mul1(c, g1[("QP", a)]), g2("QB", cell, j)))
        step = P.U * P.E1p
        for a in range(step):
            if a % P.U == 0:
                continue
            for j in range(P.Wr):
                c = pcoeff(R[cell], a + step * j)
                if c:
                    neg.append((G.mul1(c, g1[("RP", a)]), g2("RB", cell, j)))
    for t in TIES:
        for e in range(P.n - 1):
            for i in coords:
                if Qv[t][e][i]:
                    neg.append((G.mul1(Qv[t][e][i], g1[("GY", i)]), g2("GM", t, e)))
    for g in CROSS:
        for e in range(2 * P.n - 1):
            for i in coords:
                if Xv[g][e][i]:
                    neg.append((G.mul1(Xv[g][e][i], g1[("GY", i)]), g2("GB", g, e)))
    total = G.pair_sum(pos + [(G.mul1(Q - 1, a), b) for a, b in neg])
    if sector == "alpha" and key:
        total = G.addT(total, G.mulT(-key % Q, ct.gT))
    return total


def psi(G, P: Params, ct, w, x, sector: str):
    """`psi`."""
    pairs = []
    for cell in cells(P):
        k1, leg = cell
        for i in range(P.N + 1):
            c = w[cell] * x[i] % Q
            if c:
                pairs.append((G.mul1(c, ct.g1[("L1", k1, i)]), ct.g2[("SC", sector, leg)]))
    return G.pair_sum(pairs)


def lambda_coeffs(P: Params, qap, v, x):
    """`lambdaCoeffs`: the coefficients an honest decryptor uses."""
    from .field import pscale
    z = z_of(P, v, x)
    prod_u, prod_v = weight_products(P, z)
    w = {cell: weight(P, prod_u, prod_v, cell) for cell in cells(P)}
    hk, ho, R = qap.honest_quotient(x)
    W = honest_stack(P, w, x)
    return dict(
        W=W,
        sc={cell: [0] * (P.N + 1) for cell in cells(P)},
        h={cell: pscale(prod_u[0], hk) if is_key_cell(cell) else pscale(w[cell], ho)
           for cell in cells(P)},
        R={cell: pscale(w[cell], R) for cell in cells(P)},
        Qv=tie_quot(P, v, W),
        Xv=cross_poly(P, v, W),
        key=0,
    ), w, prod_u[0]
