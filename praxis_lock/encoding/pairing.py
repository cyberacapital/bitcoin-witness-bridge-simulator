"""The BN254 pairing check, as gates.

This follows the reference algorithm of the py_ecc library step for step (its
`miller_loop`), so every intermediate value can be compared with that library.
Two changes keep the size down:

  * Points of G2 stay on the twist curve, with coordinates in F_p^2. The line
    through two such points, evaluated at a point of G1, has only five non-zero
    coefficients in F_p^12:  -y_P + E(m x_P) w + E(y_1 - m x_1) w^3,
    where m is the slope on the twist and E embeds F_p^2 into F_p^12.
  * The final exponentiation is not computed. "f^((p^12-1)/r) = 1" holds exactly
    when f is an r-th power, so the prover supplies c with c^r = f and the gates
    check that by repeated squaring.

Every division is replaced by a hint and a multiplication: the prover supplies
the slope m and the gates check m * denominator = numerator.

Known gaps, which a production encoding must close: there is no check that a
G2 point lies in the prime-order subgroup, and the special cases of point
addition (equal or opposite points, zero denominators) are not excluded.
"""
from __future__ import annotations

import math

from py_ecc import bn128 as bn
from py_ecc.bn128 import bn128_pairing as bp

from .circuit import Circuit
from .fields import (P, ZERO, Lin, fp_alloc, fp_assert, fp_const, fq2_alloc, fq2_assert,
                     fq2_const, fq12_alloc, fq12_assert, fq12_const)

FQ, FQ2, FQ12 = bn.FQ, bn.FQ2, bn.FQ12
R_ORDER = bn.curve_order
ATE, LOG_ATE = bp.ate_loop_count, bp.log_ate_loop_count
assert bn.field_modulus == P


def ints(element):
    """Coefficients of a py_ecc field element as plain integers."""
    return [int(getattr(c, "n", c)) for c in element.coeffs]


def _unembed(value: "FQ12") -> "FQ2":
    """Inverse of E, for an element of F_p^12 that lies in the embedded F_p^2."""
    co = ints(value)
    assert all(co[i] == 0 for i in range(12) if i not in (0, 6))
    return FQ2([(co[0] + 9 * co[6]) % P, co[6]])


_W = FQ12([0, 1] + [0] * 10)
# Raising a twisted point to the p-th power acts on twist coordinates as
# x -> conj(x) * FROB_X, y -> conj(y) * FROB_Y; twice gives x -> x * FROB2_X, y -> y * FROB2_Y.
FROB_X = _unembed(_W ** (2 * (P - 1)))
FROB_Y = _unembed(_W ** (3 * (P - 1)))
FROB2_X = _unembed(_W ** (2 * (P * P - 1)))
FROB2_Y = _unembed(_W ** (3 * (P * P - 1)))


LOOP = format(ATE, "b")[1:]      # one doubling per character, plus an addition where it is "1"
assert len(LOOP) == LOG_ATE + 1


def miller_reference(q2, p1, loop: str = LOOP) -> "FQ12":
    """py_ecc's Miller loop, stopping before the final exponentiation.

    `loop` shortens the loop; tests use that to get small instances.
    """
    Q, Pt = bn.twist(q2), bp.cast_point_to_fq12(p1)
    R, f = Q, FQ12.one()
    for bit in loop:
        f = f * f * bp.linefunc(R, R, Pt)
        R = bn.double(R)
        if bit == "1":
            f = f * bp.linefunc(R, Q, Pt)
            R = bn.add(R, Q)
    Q1 = (Q[0] ** P, Q[1] ** P)
    nQ2 = (Q1[0] ** P, -Q1[1] ** P)
    f = f * bp.linefunc(R, Q1, Pt)
    R = bn.add(R, Q1)
    return f * bp.linefunc(R, nQ2, Pt)


# ---------------------------------------------------------------- points

class G1Point:
    """A point of G1: coordinates in F_p as `Lin`, plus their values."""

    def __init__(self, x: Lin, y: Lin, xv: int, yv: int):
        self.x, self.y, self.xv, self.yv = x, y, xv % P, yv % P

    def neg(self) -> "G1Point":
        return G1Point(self.x, -self.y, self.xv, -self.yv)


class G2Point:
    """A point of the twist: coordinates in F_p^2, plus their values."""

    def __init__(self, x, y, xv: "FQ2", yv: "FQ2", const: bool):
        self.x, self.y, self.xv, self.yv, self.const = x, y, xv, yv, const


def alloc_g1(c: Circuit, pt) -> G1Point:
    """Fresh bits for a G1 point, with the curve equation y^2 = x^3 + 3."""
    xv, yv = int(pt[0]), int(pt[1])
    x, y = fp_alloc(c, xv), fp_alloc(c, yv)
    xx = fp_alloc(c, c.hint("G1: x squared", xv * xv % P))
    fp_assert(c, [(1, x, x)], [(-1, xx)], label="G1: x squared")
    fp_assert(c, [(1, y, y), (-1, xx, x)], [(-1, fp_const(c, 3))], label="G1: on curve")
    return G1Point(x, y, xv, yv)


def alloc_g2(c: Circuit, pt) -> G2Point:
    """Fresh bits for a twist point, with the twist equation y^2 = x^3 + 3/(9+u)."""
    xv, yv = pt
    x, y = fq2_alloc(c, ints(xv)), fq2_alloc(c, ints(yv))
    xx = fq2_alloc(c, ints(c.hint("G2: x squared", xv * xv)))
    fq2_assert(c, [(1, x, x)], [(-1, xx)], label="G2: x squared")
    fq2_assert(c, [(1, y, y), (-1, xx, x)], [(-1, fq2_const(c, ints(bn.b2)))], label="G2: on twist")
    return G2Point(x, y, xv, yv, const=False)


def const_g2(c: Circuit, pt) -> G2Point:
    return G2Point(fq2_const(c, ints(pt[0])), fq2_const(c, ints(pt[1])), pt[0], pt[1], const=True)


def _fq2(c: Circuit, value: "FQ2", const: bool):
    return fq2_const(c, ints(value)) if const else fq2_alloc(c, ints(value))


# ---------------------------------------------------------------- lines

def _line(c: Circuit, m, mv, R: G2Point, Pt: G1Point):
    """The line through R with twist slope m, evaluated at Pt: a sparse F_p^12 value."""
    s1v = c.hint("line: slope times x_P", mv * Pt.xv)
    s3v = R.yv - mv * R.xv
    if not R.const:
        s3v = c.hint("line: intercept", s3v)
    s1 = fq2_alloc(c, ints(s1v))
    fq2_assert(c, [(1, m, (Pt.x, ZERO))], [(-1, s1)], label="line: slope times x_P")
    if R.const:
        s3 = fq2_const(c, ints(s3v))
    else:
        s3 = fq2_alloc(c, ints(s3v))
        fq2_assert(c, [(-1, m, R.x)], [(1, R.y), (-1, s3)], label="line: intercept")
    line = [ZERO] * 12
    line[0] = -Pt.y
    line[1] = s1[0] - s1[1].scale(9)
    line[7] = s1[1]
    line[3] = s3[0] - s3[1].scale(9)
    line[9] = s3[1]
    e = lambda v: FQ12([(int(v.coeffs[0]) - 9 * int(v.coeffs[1])) % P, 0, 0, 0, 0, 0,
                        int(v.coeffs[1]), 0, 0, 0, 0, 0])
    value = FQ12([(-Pt.yv) % P] + [0] * 11) + e(s1v) * _W + e(s3v) * _W**3
    return line, value


def _double_step(c: Circuit, R: G2Point, Pt: G1Point):
    """Tangent line at R evaluated at Pt, and the point 2R."""
    hint = (lambda label, v: v) if R.const else c.hint
    mv = hint("double: slope", 3 * R.xv * R.xv / (2 * R.yv))
    nxv = hint("double: x", mv * mv - 2 * R.xv)
    nyv = hint("double: y", mv * (R.xv - nxv) - R.yv)
    m = _fq2(c, mv, R.const)
    nx, ny = _fq2(c, nxv, R.const), _fq2(c, nyv, R.const)
    if not R.const:
        fq2_assert(c, [(2, m, R.y), (-3, R.x, R.x)], label="double: slope")
        fq2_assert(c, [(1, m, m)], [(-2, R.x), (-1, nx)], label="double: x")
        fq2_assert(c, [(1, m, R.x), (-1, m, nx)], [(-1, R.y), (-1, ny)], label="double: y")
    line, lv = _line(c, m, mv, R, Pt)
    return line, lv, G2Point(nx, ny, nxv, nyv, R.const)


def _add_step(c: Circuit, R: G2Point, Q: G2Point, Pt: G1Point, want_sum: bool = True):
    """Chord through R and Q evaluated at Pt, and the point R + Q."""
    const = R.const and Q.const
    hint = (lambda label, v: v) if const else c.hint
    mv = hint("add: slope", (Q.yv - R.yv) / (Q.xv - R.xv))
    m = _fq2(c, mv, const)
    if not const:
        fq2_assert(c, [(1, m, Q.x), (-1, m, R.x)], [(-1, Q.y), (1, R.y)], label="add: slope")
    line, lv = _line(c, m, mv, R, Pt)
    if not want_sum:
        return line, lv, None
    nxv = hint("add: x", mv * mv - R.xv - Q.xv)
    nyv = hint("add: y", mv * (R.xv - nxv) - R.yv)
    nx, ny = _fq2(c, nxv, const), _fq2(c, nyv, const)
    if not const:
        fq2_assert(c, [(1, m, m)], [(-1, R.x), (-1, Q.x), (-1, nx)], label="add: x")
        fq2_assert(c, [(1, m, R.x), (-1, m, nx)], [(-1, R.y), (-1, ny)], label="add: y")
    return line, lv, G2Point(nx, ny, nxv, nyv, const)


def _frobenius_points(c: Circuit, Q: G2Point):
    """Q1 = Frobenius(Q) and nQ2 = -Frobenius^2(Q), in twist coordinates."""
    conj = lambda v: FQ2([int(v.coeffs[0]), (-int(v.coeffs[1])) % P])
    x1v, y1v = conj(Q.xv) * FROB_X, conj(Q.yv) * FROB_Y
    x2v, y2v = Q.xv * FROB2_X, -(Q.yv * FROB2_Y)
    if Q.const:
        return const_g2(c, (x1v, y1v)), const_g2(c, (x2v, y2v))
    x1v, y1v = c.hint("Frobenius x", x1v), c.hint("Frobenius y", y1v)
    x2v, y2v = c.hint("Frobenius^2 x", x2v), c.hint("Frobenius^2 y", y2v)
    cx, cy = (Q.x[0], -Q.x[1]), (Q.y[0], -Q.y[1])
    q1 = G2Point(fq2_alloc(c, ints(x1v)), fq2_alloc(c, ints(y1v)), x1v, y1v, False)
    q2 = G2Point(fq2_alloc(c, ints(x2v)), fq2_alloc(c, ints(y2v)), x2v, y2v, False)
    fq2_assert(c, [(1, fq2_const(c, ints(FROB_X)), cx)], [(-1, q1.x)], label="Frobenius x")
    fq2_assert(c, [(1, fq2_const(c, ints(FROB_Y)), cy)], [(-1, q1.y)], label="Frobenius y")
    fq2_assert(c, [(1, fq2_const(c, ints(FROB2_X)), Q.x)], [(-1, q2.x)], label="Frobenius^2 x")
    fq2_assert(c, [(1, fq2_const(c, ints(FROB2_Y)), Q.y)], [(1, q2.y)], label="Frobenius^2 y")
    return q1, q2


# ---------------------------------------------------------------- Miller loop

class Acc:
    """The running product f in F_p^12. Starts as the constant 1."""

    def __init__(self):
        self.lin, self.val = None, FQ12.one()

    def mul(self, c: Circuit, other, other_val, label: str):
        if self.lin is None:
            self.lin, self.val = other, other_val
            return
        self.val = c.hint(label, self.val * other_val)
        new = fq12_alloc(c, ints(self.val))
        fq12_assert(c, [(1, self.lin, other)], [(-1, new)], label=label)
        self.lin = new

    def square(self, c: Circuit):
        if self.lin is not None:
            self.mul(c, self.lin, self.val, "Miller: square")


def miller_product(c: Circuit, pairs, loop: str = LOOP):
    """The product of the Miller values of all (Q in G2, P in G1) pairs.

    All pairs share one accumulator, so the squarings are paid once.
    Returns the accumulator: `.lin` are its twelve coefficients, `.val` its value.
    `loop` shortens the loop, for small test instances.
    """
    acc = Acc()
    points = [Q for Q, _ in pairs]
    for bit in loop:
        acc.square(c)
        for k, (Q, Pt) in enumerate(pairs):
            line, lv, points[k] = _double_step(c, points[k], Pt)
            acc.mul(c, line, lv, "Miller: tangent")
        if bit == "1":
            for k, (Q, Pt) in enumerate(pairs):
                line, lv, points[k] = _add_step(c, points[k], Q, Pt)
                acc.mul(c, line, lv, "Miller: chord")
    for k, (Q, Pt) in enumerate(pairs):
        q1, nq2 = _frobenius_points(c, Q)
        line, lv, points[k] = _add_step(c, points[k], q1, Pt)
        acc.mul(c, line, lv, "Miller: Frobenius chord")
        line, lv, _ = _add_step(c, points[k], nq2, Pt, want_sum=False)
        acc.mul(c, line, lv, "Miller: last chord")
    return acc


# ---------------------------------------------------------------- the final check

def fq12_pow(base: "FQ12", exponent: int) -> "FQ12":
    """Square-and-multiply without recursion (the exponent has thousands of bits)."""
    result = FQ12.one()
    for bit in bin(exponent)[2:]:
        result = result * result
        if bit == "1":
            result = result * base
    return result


def assert_pairing_identity(c: Circuit, acc: Acc, numer: "FQ12", denom: "FQ12",
                            order: int = R_ORDER) -> None:
    """Assert that  acc * numer / denom  becomes 1 under the final exponentiation.

    Equivalent: it is an r-th power. The prover supplies the root; the gates
    check  root^r * denom = acc * numer  by square-and-multiply.

    `order` replaces r by a small exponent, for small test instances.
    """
    target = acc.val * numer / denom
    h = (P**12 - 1) // math.gcd(P**12 - 1, order)
    root_val = c.hint("root", fq12_pow(target, pow(order, -1, h)))
    root = fq12_alloc(c, ints(root_val))
    cur, cur_val = root, root_val
    for bit in bin(order)[3:]:
        cur_val = c.hint("root: square", cur_val * cur_val)
        nxt = fq12_alloc(c, ints(cur_val))
        fq12_assert(c, [(1, cur, cur)], [(-1, nxt)], label="root: square")
        cur = nxt
        if bit == "1":
            cur_val = c.hint("root: multiply", cur_val * root_val)
            nxt = fq12_alloc(c, ints(cur_val))
            fq12_assert(c, [(1, cur, root)], [(-1, nxt)], label="root: multiply")
            cur = nxt
    fq12_assert(c, [(1, cur, fq12_const(c, ints(denom))), (-1, acc.lin, fq12_const(c, ints(numer)))],
                label="pairing equation")
