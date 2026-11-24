"""A small Groth16 proof system, and its verifier written as lock gates.

Groth16 proves statements given as a rank-1 constraint system: each constraint
says <A, z> * <B, z> = <C, z> for a vector z whose first entries are public.
A proof is three curve points (A, B, C), and the verifier accepts when

    e(A, B) = e(alpha, beta) * e(L, gamma) * e(C, delta)

where L is fixed by the public inputs. `encode_verifier` turns exactly that
check into gates over bits, so that a lock made for those gates opens for
anyone who holds a valid proof.

The setup and prover here are for testing. The prover uses the setup's secret
numbers to build the proof directly, which gives the same proof an ordinary
prover would compute from the public parameters.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from py_ecc import bn128 as bn
from py_ecc import optimized_bn128 as ob

from ..field import Q as R, interpolate, inv, pdivmod, peval, pmul, padd, psub, pscale
from .circuit import Circuit
from .pairing import (alloc_g1, alloc_g2, assert_pairing_compact, assert_pairing_folded,
                      assert_pairing_identity, const_g2, miller_product, miller_value)


@dataclass
class R1CS:
    """Constraints <a, z> * <b, z> = <c, z>; z = (1, public..., private...)."""
    n_public: int      # number of public inputs, not counting the leading 1
    n_vars: int        # length of z
    a: list
    b: list
    c: list

    def satisfied(self, z) -> bool:
        dot = lambda row: sum(k * z[i] for i, k in row.items()) % R
        return (len(z) == self.n_vars and z[0] == 1
                and all(dot(a) * dot(b) % R == dot(c) for a, b, c in zip(self.a, self.b, self.c)))


@dataclass
class VerifyingKey:
    alpha_g1: tuple
    beta_g2: tuple
    gamma_g2: tuple
    delta_g2: tuple
    ic: list


@dataclass
class Trapdoor:
    """The setup's secrets. A real setup destroys these; tests keep them."""
    tau: int
    alpha: int
    beta: int
    gamma: int
    delta: int


def _columns(r1cs: R1CS):
    """The QAP: for each variable, polynomials through its column of A, B, C."""
    xs = list(range(1, len(r1cs.a) + 1))
    col = lambda rows, i: interpolate(xs, [row.get(i, 0) % R for row in rows])
    u = [col(r1cs.a, i) for i in range(r1cs.n_vars)]
    v = [col(r1cs.b, i) for i in range(r1cs.n_vars)]
    w = [col(r1cs.c, i) for i in range(r1cs.n_vars)]
    target = [1]
    for x in xs:
        target = pmul(target, [(-x) % R, 1])
    return u, v, w, target


def setup(r1cs: R1CS, rng=secrets):
    td = Trapdoor(*(rng.randbelow(R - 1) + 1 for _ in range(5)))
    u, v, w, _ = _columns(r1cs)
    ic = []
    for i in range(r1cs.n_public + 1):
        k = (td.beta * peval(u[i], td.tau) + td.alpha * peval(v[i], td.tau) + peval(w[i], td.tau))
        ic.append(bn.multiply(bn.G1, k * inv(td.gamma) % R))
    vk = VerifyingKey(bn.multiply(bn.G1, td.alpha), bn.multiply(bn.G2, td.beta),
                      bn.multiply(bn.G2, td.gamma), bn.multiply(bn.G2, td.delta), ic)
    return vk, td


def prove(r1cs: R1CS, td: Trapdoor, z, rng=secrets):
    """A Groth16 proof (A, B, C) for the assignment z."""
    if not r1cs.satisfied(z):
        raise ValueError("the assignment does not satisfy the constraints")
    u, v, w, target = _columns(r1cs)
    combine = lambda polys: [sum(z[i] * (p[k] if k < len(p) else 0) for i, p in enumerate(polys)) % R
                             for k in range(len(r1cs.a))]
    U, V, W = combine(u), combine(v), combine(w)
    h, rem = pdivmod(psub(pmul(U, V), W), target)
    assert not rem
    r, s = rng.randbelow(R), rng.randbelow(R)
    tau = td.tau
    a = (td.alpha + peval(U, tau) + r * td.delta) % R
    b = (td.beta + peval(V, tau) + s * td.delta) % R
    private = sum(z[i] * (td.beta * peval(u[i], tau) + td.alpha * peval(v[i], tau) + peval(w[i], tau))
                  for i in range(r1cs.n_public + 1, r1cs.n_vars)) % R
    c = ((private + peval(h, tau) * peval(target, tau)) * inv(td.delta) + s * a + r * b
         - r * s * td.delta) % R
    return bn.multiply(bn.G1, a), bn.multiply(bn.G2, b), bn.multiply(bn.G1, c)


def public_point(vk: VerifyingKey, public):
    """L = IC_0 + sum public_i * IC_i."""
    point = vk.ic[0]
    for value, ic in zip(public, vk.ic[1:]):
        point = bn.add(point, bn.multiply(ic, value % R))
    return point


def verify(vk: VerifyingKey, public, proof) -> bool:
    """The ordinary Groth16 verifier, entirely on the library's own pairing code.

    Checks  e(A, B) * e(-alpha, beta) * e(-L, gamma) * e(-C, delta) = 1  with
    py_ecc's optimized implementation and one shared final exponentiation.
    Nothing from this package's encoding is involved, so it can referee it.
    """
    A, B, C = proof
    L = public_point(vk, public)
    g1 = lambda pt: (ob.FQ(int(pt[0])), ob.FQ(int(pt[1])), ob.FQ(1))
    g2 = lambda pt: (ob.FQ2([int(v) for v in pt[0].coeffs]), ob.FQ2([int(v) for v in pt[1].coeffs]),
                     ob.FQ2.one())
    product = ob.FQ12.one()
    for q, pt in ((B, A), (vk.beta_g2, bn.neg(vk.alpha_g1)), (vk.gamma_g2, bn.neg(L)),
                  (vk.delta_g2, bn.neg(C))):
        product = product * ob.pairing(g2(q), g1(pt), final_exponentiate=False)
    return ob.final_exponentiate(product) == ob.FQ12.one()


CHECKS = ("compact", "folded", "root")


def encode_verifier(vk: VerifyingKey, public, proof, materialize: bool = False,
                    style: str = "wide", check: str = "compact") -> Circuit:
    """The Groth16 verifier as gates over bits, built with `proof` as the assignment.

    The unknown bits are the proof's coordinates plus every intermediate value
    of the check. The verifying key and the public inputs are constants, so
    they cost no bits. The circuit's `satisfied` flag says whether this proof
    passes.

    Checked: A and C lie on the curve, B on the twist, and

        Miller(B, A) * Miller(delta, -C) * Miller(gamma, -L) / Miller(beta, alpha)

    is an r-th power, which is the verifier's pairing equation.

    `style` is the arithmetic layout, "wide" or "narrow" (see `emulated.py`).
    `check` chooses how the r-th power is checked (see `pairing.py`):

      "root"     a separate root raised back to its power, 353 steps
      "folded"   the root folded into the pairing loop
      "compact"  folded, with the running product stored in half the space,
                 the two lines of each step merged, and a signed loop

    The defaults give the smallest statement.
    """
    if check not in CHECKS:
        raise ValueError(f"check is one of {CHECKS}")
    A, B, C = proof
    c = Circuit(materialize=materialize, style=style)
    a_pt, b_pt, c_pt = alloc_g1(c, A), alloc_g2(c, B), alloc_g1(c, C)
    c.mark("the proof's three points, each checked to lie on its curve")
    pairs = [(b_pt, a_pt), (const_g2(c, vk.delta_g2), c_pt.neg())]
    L = public_point(vk, public)
    numer = miller_value(vk.gamma_g2, bn.neg(L))
    denom = miller_value(vk.beta_g2, vk.alpha_g1)
    if check == "compact":
        assert_pairing_compact(c, pairs[0], pairs[1], numer, denom)
        c.mark("the two pairings that involve the proof and the final check, in one loop")
    elif check == "folded":
        assert_pairing_folded(c, pairs, numer, denom)
        c.mark("the two pairings that involve the proof and the final check, in one loop")
    else:
        acc = miller_product(c, pairs)
        c.mark("the two pairings that involve the proof (one shared Miller loop)")
        assert_pairing_identity(c, acc, numer, denom)
        c.mark("the final check, done as an r-th root raised back to the r-th power")
    return c


def factoring_r1cs() -> R1CS:
    """z = (1, n | x, y, x2) with x * y = n and x * x = x2."""
    return R1CS(n_public=1, n_vars=5,
                a=[{2: 1}, {2: 1}], b=[{3: 1}, {2: 1}], c=[{1: 1}, {4: 1}])
