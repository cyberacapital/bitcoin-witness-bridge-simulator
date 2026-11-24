"""Encryption and decryption: Praxis/Enc.lean and Praxis/Dec.lean.

`encrypt` follows `encrypt` / `g1Exp` / `g2Exp` line by line.

`decrypt` computes what `decRef` computes, for the honest coefficients
(`lambdaCoeffs`). Because the honest matrix in every cell is a multiple of
x x^T, the triple sums of `evalComb` collapse into a handful of pairings; the
literal triple sums live in `reference.py` and the tests check both agree.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from math import isqrt

from .field import Q, inv, pcoeff
from .kummer import (KEY_CELL, cell_exp, cells, forms_of, is_key_cell, is_u_cell, lam, leg_exp,
                     legs, sigma, weight, weight_products, z_of)
from .params import Params
from .qap import QAP

SECTORS = ("plain", "alpha")
CROSS = ("c1eps", "c2eps", "c3eps", "c2div", "c3div")
TIES = ("t1", "t2", "t3")
SCALAR_COINS = ("tau", "Om12", "Om22", "zeta", "zetap", "zeta4", "pi", "bE", "bO", "bV",
                "c1", "c2", "c3", "u", "alpha", "eps")
UNIT_COINS = tuple(s for s in SCALAR_COINS if s not in ("tau", "Om12", "eps"))


# ---------------------------------------------------------------- coins

@dataclass
class Coins:
    y: list      # a random vector, one entry per coordinate of x
    w0: int      # the search sample: w0 and w[i] lie in [0, g)
    w: list
    s: dict      # the sixteen scalar coins


def valid_coins(P: Params, qap: QAP, c: Coins) -> bool:
    """`ValidCoins`."""
    if c.s["tau"] in qap.xs or any(c.s[name] % Q == 0 for name in UNIT_COINS):
        return False
    eps = c.s["eps"] % Q
    if eps == 0:
        return False
    return all(eps != (-inv(pow(P.beta, 2 * k1, Q))) % Q for k1 in range(P.K1))


def _below(rng, bound: int) -> int:
    """Works with the `secrets` module and with `random.Random` (for tests)."""
    return rng.randbelow(bound) if hasattr(rng, "randbelow") else rng.randrange(bound)


def _token(rng, length: int) -> bytes:
    return rng.token_bytes(length) if hasattr(rng, "token_bytes") else rng.randbytes(length)


def sample_coins(P: Params, qap: QAP, rng=secrets) -> Coins:
    while True:
        c = Coins(
            y=[_below(rng, Q) for _ in range(P.N + 1)],
            w0=_below(rng, P.g),
            w=[_below(rng, P.g) for _ in range(P.N)],
            s={name: _below(rng, Q) for name in SCALAR_COINS},
        )
        if valid_coins(P, qap, c):
            return c


def center_a(P: Params) -> int:
    return (P.N * (P.g + 1) + 1) // 2


def r_int(P: Params, c: Coins):
    """`centeredZ`: the search sample as integers, entry 0 centred against the rest."""
    rest = [2 + wi for wi in c.w]
    return [center_a(P) + (2 + c.w0) - sum(rest) // 2] + rest


def c_x(P: Params, x) -> int:
    """`cX`: where <x, r> is centred for this x. The search runs around it."""
    half = (P.g + 1) // 2 + 1
    return (2 * (center_a(P) + half + half * sum(x[1:])) - half * P.N) // 2


# ---------------------------------------------------------------- ciphertext

@dataclass
class Ciphertext:
    g1: dict
    g2: dict
    gT: object
    seed: bytes
    cm: bytes    # the message, masked with a key derived from [kappa]_T

    def counts(self):
        return len(self.g1), len(self.g2)


def derive_key(G, K, length: int) -> bytes:
    """Key from the unordered pair {K, -K}, as in the Lean development (`labelName`)."""
    a, b = sorted((G.serT(K), G.serT(G.mulT(Q - 1, K))))
    out, counter = b"", 0
    while len(out) < length:
        out += hashlib.sha256(b"praxis-lock/key" + a + b + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:length]


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(p ^ q for p, q in zip(a, b))


# ---------------------------------------------------------------- encryption

def exponents(P: Params, qap: QAP, v, c: Coins):
    """Every exponent of the ciphertext: `g1Exp`, `g2Exp`, `gtExp`, and `kappa`."""
    s = c.s
    tau, u, eps, pi_, alpha = s["tau"], s["u"], s["eps"], s["pi"], s["alpha"]
    beta, y = P.beta, c.y
    r = [ri % Q for ri in r_int(P, c)]
    A, B, l_rho, z_tau, z0_tau = qap.eval_at(tau)

    def pi_at(k1):
        return pi_ * pow(u, 2 * k1, Q) % Q * (1 + eps * pow(beta, 2 * k1, Q)) % Q

    b_of = {"E": s["bE"], "O": s["bO"], "V": s["bV"]}

    def leg_scale(leg):
        return b_of[leg[0]] * pow(u, leg_exp(P, leg), Q) % Q

    def gamma(cell):
        return pi_at(cell[0]) * leg_scale(cell[1]) % Q

    l_minus_u = lam(P, v, -u % Q)
    l_beta_u = lam(P, v, -beta * u % Q)
    l_top = lam(P, v, pow(beta, P.m - 1, Q) * u % Q)

    def leg_mask(leg):
        x = leg_exp(P, leg)
        ux = pow(u, x, Q)
        if leg[0] == "V":
            return [ux * (s["c3"] * a - s["c1"] * b) % Q for a, b in zip(l_beta_u, l_top)]
        tail = inv(eps) * pow(-beta * u % Q, x, Q) % Q
        return [(ux * (s["c1"] * a + s["c2"] * b) - tail * (s["c2"] * t + s["c3"] * a)) % Q
                for a, b, t in zip(l_minus_u, l_beta_u, l_top)]

    om_ratio = s["Om12"] * inv(s["Om22"]) % Q
    om22_inv = inv(s["Om22"])
    kappa = pi_at(0) * leg_scale(("E", 0)) % Q * l_rho % Q

    g1 = {}
    for k1 in range(P.K1):
        p = pi_at(k1)
        for i in range(P.N + 1):
            g1[("L1", k1, i)] = p * y[i] % Q
            g1[("L2", k1, i)] = p * (s["Om12"] * y[i] + s["Om22"] * A[i]) % Q
        g1[("totL1", k1)] = sum(g1[("L1", k1, i)] for i in range(P.N + 1)) % Q
        g1[("totL2", k1)] = sum(g1[("L2", k1, i)] for i in range(P.N + 1)) % Q
    for a in range(P.E1):
        g1[("QP", a)] = s["zeta"] * pow(tau, a, Q) % Q
    for a in range(P.U * P.E1p):
        if a % P.U:
            g1[("RP", a)] = s["zetap"] * pow(tau, a, Q) % Q
    for i in range(P.N + 1):
        g1[("GY", i)] = s["zeta4"] * y[i] % Q

    cross = {"c1eps": s["c1"] * eps, "c2eps": s["c2"] * eps, "c3eps": s["c3"] * eps,
             "c2div": s["c2"] * inv(eps), "c3div": s["c3"] * inv(eps)}
    tie = {"t1": s["c1"], "t2": s["c2"], "t3": s["c3"]}
    kummer_at_u = (pow(u, P.n, Q) - P.c0) % Q
    zeta_inv, zetap_inv, zeta4_inv = inv(s["zeta"]), inv(s["zetap"]), inv(s["zeta4"])

    plain = {}
    for leg in legs(P):
        scale, mask = leg_scale(leg), leg_mask(leg)
        h1 = [(scale * (r[j] + A[j]) + mask[j]) % Q for j in range(P.N + 1)]
        h2 = [scale * (B[j] - y[j]) % Q for j in range(P.N + 1)]
        for j in range(P.N + 1):
            plain[("ZC", leg, j)] = (h1[j] - om_ratio * h2[j]) % Q
            plain[("R2", leg, j)] = h2[j] * om22_inv % Q
        plain[("totR1", leg)] = sum(plain[("ZC", leg, j)] for j in range(P.N + 1)) % Q
        plain[("totR2", leg)] = sum(plain[("R2", leg, j)] for j in range(P.N + 1)) % Q
        plain[("SC", leg)] = scale
    for cell in cells(P):
        z_cell = z_tau * (tau - qap.rho) % Q if is_key_cell(cell) else z_tau
        for j in range(P.Mq):
            plain[("QB", cell, j)] = gamma(cell) * z_cell % Q * pow(tau, P.E1 * j, Q) % Q * zeta_inv % Q
        for j in range(P.Wr):
            plain[("RB", cell, j)] = (gamma(cell) * z0_tau % Q
                                      * pow(tau, P.U * P.E1p * j, Q) % Q * zetap_inv % Q)
    for g in CROSS:
        for e in range(2 * P.n - 1):
            plain[("GB", g, e)] = pi_ * cross[g] % Q * pow(u, e, Q) % Q * zeta4_inv % Q
    for t in TIES:
        for e in range(P.n - 1):
            plain[("GM", t, e)] = pi_ * tie[t] % Q * kummer_at_u % Q * pow(u, e, Q) % Q * zeta4_inv % Q

    g2 = {}
    for sector, scale in (("plain", 1), ("alpha", alpha)):
        for key, value in plain.items():
            g2[(key[0], sector) + key[1:]] = scale * value % Q
    return g1, g2, alpha * kappa % Q, kappa


def encrypt(G, P: Params, gates, message: bytes, rng=secrets, coins: Coins = None,
            seed: bytes = None) -> Ciphertext:
    """Lock `message` so that it opens for anyone who knows a solution of `gates`."""
    if len(message) != P.message_bytes:
        raise ValueError(f"message must be exactly {P.message_bytes} bytes")
    qap = QAP(P, gates)
    seed = seed if seed is not None else _token(rng, P.seed_bytes)
    coins = coins if coins is not None else sample_coins(P, qap, rng)
    v = forms_of(P, seed)
    g1, g2, gt, kappa = exponents(P, qap, v, coins)
    return Ciphertext(
        g1={k: G.enc1(e) for k, e in g1.items()},
        g2={k: G.enc2(e) for k, e in g2.items()},
        gT=G.encT(gt),
        seed=seed,
        cm=_xor(message, derive_key(G, G.encT(kappa), P.message_bytes)),
    )


# ---------------------------------------------------------------- decryption

def _lin1(G, terms):
    acc = G.zero1
    for k, point in terms:
        if k % Q:
            acc = G.add1(acc, point if k % Q == 1 else G.mul1(k, point))
    return acc


def _lin2(G, terms):
    acc = G.zero2
    for k, point in terms:
        if k % Q:
            acc = G.add2(acc, point if k % Q == 1 else G.mul2(k, point))
    return acc


def _convolve(a, b):
    out = [0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b):
                out[i + j] = (out[i + j] + x * y) % Q
    return out


def tie_scalars(P: Params, prod_u, prod_v, z):
    """`tieQuot` for the honest stack. Each quotient vector is a scalar times x;
    this returns the scalars, indexed [tie][e] for e < n - 1."""
    n, m = P.n, P.m
    u_poly = list(prod_u)
    v_poly = [prod_v[k] if k % 2 == 0 else 0 for k in range(n)]
    su_poly = sigma(P, m + 1, u_poly)
    zs = {s: sigma(P, s, z) for s in (m - 1, m, m + 1)}

    def diff(a, b, c, d):
        return [(p - q) % Q for p, q in zip(_convolve(a, b), _convolve(c, d))]

    full = {
        "t1": diff(u_poly, zs[m], v_poly, zs[m - 1]),
        "t2": diff(u_poly, zs[m + 1], su_poly, zs[m - 1]),
        "t3": diff(v_poly, zs[m + 1], su_poly, zs[m]),
    }
    return {t: [poly[e + n] for e in range(n - 1)] for t, poly in full.items()}


def cross_scalars(P: Params, weights, z):
    """`crossPoly` for the honest stack, again as scalars times x: [cross][e], e < 2n - 1."""
    n, beta = P.n, P.beta
    top = pow(beta, P.m - 1, Q)
    out = {g: [0] * (2 * n - 1) for g in CROSS}
    for cell, w in weights.items():
        k = cell_exp(P, cell)
        theta = pow(beta, 2 * cell[0], Q)
        beta_leg = pow(-beta % Q, leg_exp(P, cell[1]), Q)
        for idx in range(n):
            wz = w * z[idx] % Q
            if not wz:
                continue
            e = k + idx
            sign = Q - 1 if idx % 2 else 1
            neg_beta = pow(-beta % Q, idx, Q)
            top_pow = pow(top, idx, Q)
            if is_u_cell(cell):
                out["c1eps"][e] = (out["c1eps"][e] + theta * sign * wz) % Q
                out["c2eps"][e] = (out["c2eps"][e] + theta * neg_beta * wz) % Q
                out["c2div"][e] = (out["c2div"][e] - beta_leg * top_pow * wz) % Q
                out["c3div"][e] = (out["c3div"][e] - beta_leg * sign * wz) % Q
            else:
                out["c1eps"][e] = (out["c1eps"][e] - theta * top_pow * wz) % Q
                out["c3eps"][e] = (out["c3eps"][e] + theta * neg_beta * wz) % Q
    return out


def find_small_multiple(G, target, base, bound: int):
    """The search that ends decryption: t with target = t * base and |t| <= bound.

    Baby-step giant-step, so about sqrt(2 * bound) group operations.
    """
    if G.eqT(base, G.zeroT):
        return 0 if G.eqT(target, G.zeroT) else None
    size = 2 * bound + 1
    step = isqrt(size) + 1
    table, acc = {}, G.zeroT
    for j in range(step):
        table.setdefault(G.serT(acc), j)
        acc = G.addT(acc, base)
    giant = G.mulT(Q - step, base)
    cur = G.addT(target, G.mulT(bound, base))
    for i in range(step + 1):
        j = table.get(G.serT(cur))
        if j is not None and i * step + j < size:
            return i * step + j - bound
        cur = G.addT(cur, giant)
    return None


def honest_parts(G, P: Params, qap: QAP, ct: Ciphertext, x):
    """Lambda and Psi in both sectors, plus the key weight; or None if x fails."""
    v = forms_of(P, ct.seed)
    z = z_of(P, v, x)
    prod_u, prod_v = weight_products(P, z)
    wk = prod_u[0]
    if wk == 0:
        return None
    quotient = qap.honest_quotient(x)
    if quotient is None:
        return None
    hk, ho, R = quotient

    all_cells, all_legs, coords = cells(P), legs(P), range(P.N + 1)
    w = {cell: weight(P, prod_u, prod_v, cell) for cell in all_cells}
    q_tie = tie_scalars(P, prod_u, prod_v, z)
    x_cross = cross_scalars(P, w, z)

    x_l1 = [_lin1(G, [(x[i], ct.g1[("L1", k1, i)]) for i in coords]) for k1 in range(P.K1)]
    x_l2 = [_lin1(G, [(x[i], ct.g1[("L2", k1, i)]) for i in coords]) for k1 in range(P.K1)]
    x_gy = _lin1(G, [(x[i], ct.g1[("GY", i)]) for i in coords])
    h_o = [_lin1(G, [(pcoeff(ho, a + P.E1 * j), ct.g1[("QP", a)]) for a in range(P.E1)])
           for j in range(P.Mq)]
    h_k = [_lin1(G, [(pcoeff(hk, a + P.E1 * j), ct.g1[("QP", a)]) for a in range(P.E1)])
           for j in range(P.Mq)]
    step = P.U * P.E1p
    r_j = [_lin1(G, [(pcoeff(R, a + step * j), ct.g1[("RP", a)]) for a in range(step) if a % P.U])
           for j in range(P.Wr)]

    def neg(point):
        return G.mul1(Q - 1, point)

    out = {}
    for sector in SECTORS:
        g2 = lambda kind, *rest: ct.g2[(kind, sector) + rest]
        zx = {leg: _lin2(G, [(x[j], g2("ZC", leg, j)) for j in coords]) for leg in all_legs}
        rx = {leg: _lin2(G, [(x[j], g2("R2", leg, j)) for j in coords]) for leg in all_legs}
        pairs = []
        for k1 in range(P.K1):
            pairs.append((x_l1[k1], _lin2(G, [(w[(k1, leg)], zx[leg]) for leg in all_legs])))
            pairs.append((x_l2[k1], _lin2(G, [(w[(k1, leg)], rx[leg]) for leg in all_legs])))
        for j in range(P.Mq):
            pairs.append((neg(h_o[j]), _lin2(G, [(w[c], g2("QB", c, j))
                                                 for c in all_cells if not is_key_cell(c)])))
            pairs.append((neg(h_k[j]), G.mul2(wk, g2("QB", KEY_CELL, j))))
        for j in range(P.Wr):
            pairs.append((neg(r_j[j]), _lin2(G, [(w[c], g2("RB", c, j)) for c in all_cells])))
        tail = [(q_tie[t][e], g2("GM", t, e)) for t in TIES for e in range(P.n - 1)]
        tail += [(x_cross[g][e], g2("GB", g, e)) for g in CROSS for e in range(2 * P.n - 1)]
        pairs.append((neg(x_gy), _lin2(G, tail)))
        lam_sector = G.pair_sum(pairs)
        psi_sector = G.pair_sum([
            (x_l1[k1], _lin2(G, [(w[(k1, leg)], g2("SC", leg)) for leg in all_legs]))
            for k1 in range(P.K1)])
        out[sector] = (lam_sector, psi_sector)
    return wk, out


def decrypt(G, P: Params, gates, ct: Ciphertext, x):
    """Open a ciphertext with a solution x of `gates`. Returns None on failure."""
    x = [int(v) % Q for v in x]
    if len(x) != P.N + 1:
        raise ValueError("x must have N + 1 entries, the first being 1")
    qap = QAP(P, gates)
    parts = honest_parts(G, P, qap, ct, x)
    if parts is None:
        return None
    wk, sectors = parts
    lam0, psi0 = sectors["plain"]
    lam1, psi1 = sectors["alpha"]
    cx = c_x(P, [v if v <= Q // 2 else v - Q for v in x])

    # H' = Lambda_1 - wk [alpha kappa]_T - cx Psi_1  should be  t Psi_1  for a small t.
    h = G.addT(lam1, G.mulT(-wk % Q, ct.gT))
    h = G.addT(h, G.mulT(-cx % Q, psi1))
    t = find_small_multiple(G, h, psi1, P.t_bound)
    if t is None:
        return None
    key_point = G.mulT(inv(wk), G.addT(lam0, G.mulT(-(cx + t) % Q, psi0)))
    return _xor(ct.cm, derive_key(G, key_point, P.message_bytes))
