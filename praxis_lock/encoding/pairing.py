"""The BN254 pairing check, as gates.

This follows the reference algorithm of the py_ecc library step for step (its
`miller_loop`), so every intermediate value can be compared with that library.
Two changes keep the size down:

  * Points of G2 stay on the twist curve, with coordinates in F_p^2. The line
    through two such points, evaluated at a point of G1, has only five non-zero
    coefficients in F_p^12:  -y_P + E(m x_P) w + E(y_1 - m x_1) w^3,
    where m is the slope on the twist and E embeds F_p^2 into F_p^12.
  * The final exponentiation is not computed. "f^((p^12-1)/r) = 1" holds exactly
    when f is an r-th power, so the prover supplies a root and the gates check
    it. Two ways are built here. The plain one (`assert_pairing_identity`)
    takes c with c^r = f and raises it back by repeated squaring, 353 steps.
    The folded one (`assert_pairing_folded`) takes c with c^lambda = f * w for
    a multiple lambda = 6x+2 + p - p^2 + p^3 of r. The 6x+2 part rides along
    in the pairing loop's own squarings and the other three parts are
    Frobenius maps, which are linear, so the 353 steps shrink to about 40.
    This is the residue method of Novakovic and Eagen, "On Proving Pairings",
    worked out again here for these gates.

Every division is replaced by a hint and a multiplication: the prover supplies
the slope m and the gates check m * denominator = numerator.

Known gaps, which a production encoding must close: there is no check that a
G2 point lies in the prime-order subgroup, and the special cases of point
addition (equal or opposite points, zero denominators) are not excluded.
"""
from __future__ import annotations

import functools
import math

from py_ecc import bn128 as bn
from py_ecc.bn128 import bn128_pairing as bp

from .circuit import Circuit
from .emulated import Big, Limb
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

def _line_coeffs(s1v: "FQ2", s3v: "FQ2", y_p: int):
    """Coefficients of  -y_P + E(s1) w + E(s3) w^3,  where E(a0 + a1 u) = (a0 - 9 a1) + a1 w^6."""
    (a0, a1), (b0, b1) = ints(s1v), ints(s3v)
    return [(-y_p) % P, (a0 - 9 * a1) % P, 0, (b0 - 9 * b1) % P, 0, 0, 0, a1, 0, b1, 0, 0]


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
    return line, FQ12(_line_coeffs(s1v, s3v, Pt.yv))


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
        self.val = c.hint(label, fq12_mul(self.val, other_val))
        new = fq12_alloc(c, ints(self.val))
        fq12_assert(c, [(1, self.lin, other)], [(-1, new)], label=label)
        self.lin = new

    def square(self, c: Circuit):
        if self.lin is not None:
            self.mul(c, self.lin, self.val, "Miller: square")


def miller_product(c: Circuit, pairs, loop: str = LOOP, fold=None):
    """The product of the Miller values of all (Q in G2, P in G1) pairs.

    All pairs share one accumulator, so the squarings are paid once.
    Returns the accumulator: `.lin` are its twelve coefficients, `.val` its value.
    `loop` shortens the loop, for small test instances.

    `fold` is an allocated element d with its value. With it the accumulator
    starts at d and is multiplied by d again wherever the loop adds, so the
    result is the Miller product times d^e, where e is the loop count.
    """
    acc = Acc()
    if fold is not None:
        acc.mul(c, fold[0], fold[1], "Miller: fold")       # the first one only sets the start
    points = [Q for Q, _ in pairs]
    for bit in loop:
        acc.square(c)
        for k, (Q, Pt) in enumerate(pairs):
            line, lv, points[k] = _double_step(c, points[k], Pt)
            acc.mul(c, line, lv, "Miller: tangent")
        if bit == "1":
            if fold is not None:
                acc.mul(c, fold[0], fold[1], "Miller: fold")
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

_SLOT = 520                      # room for one coefficient of a product: twelve terms below p^2
_SLOT_MASK = (1 << _SLOT) - 1


def _pack(coeffs) -> int:
    """Twelve coefficients below p as one integer, one per 520-bit slot."""
    return sum(v << (_SLOT * i) for i, v in enumerate(coeffs))


def _mul_packed(a: int, b: int) -> int:
    """Product in F_p^12 of two packed elements: one big multiplication, then w^12 = 18 w^6 - 82."""
    wide = a * b
    co = [(wide >> (_SLOT * i)) & _SLOT_MASK for i in range(23)]
    for t in range(22, 11, -1):
        co[t - 6] += 18 * co[t]
        co[t - 12] -= 82 * co[t]
    return _pack(v % P for v in co[:12])


def _unpack(a: int) -> "FQ12":
    return FQ12([(a >> (_SLOT * i)) & _SLOT_MASK for i in range(12)])


def fq12_mul(a: "FQ12", b: "FQ12") -> "FQ12":
    """a * b, the same value as the library's product, several times faster."""
    return _unpack(_mul_packed(_pack(ints(a)), _pack(ints(b))))


def miller_value(q2, p1, loop: str = LOOP) -> "FQ12":
    """The same value as `miller_reference`, computed the way the gates compute it.

    Used by the prover, which needs the Miller values before it can choose its
    root. The test suite checks it against the library's loop.
    """
    (qx, qy), px, py = q2, int(p1[0]), int(p1[1])
    conj = lambda v: FQ2([int(v.coeffs[0]), (-int(v.coeffs[1])) % P])
    state = {"x": qx, "y": qy, "f": 1}

    def step(m, other_x):
        line = _pack(_line_coeffs(m * px, state["y"] - m * state["x"], py))
        state["f"] = _mul_packed(state["f"], line)
        nx = m * m - state["x"] - other_x
        state["y"] = m * (state["x"] - nx) - state["y"]
        state["x"] = nx

    for bit in loop:
        state["f"] = _mul_packed(state["f"], state["f"])
        step(3 * state["x"] * state["x"] / (2 * state["y"]), state["x"])
        if bit == "1":
            step((qy - state["y"]) / (qx - state["x"]), qx)
    q1 = (conj(qx) * FROB_X, conj(qy) * FROB_Y)
    nq2 = (qx * FROB2_X, -(qy * FROB2_Y))
    step((q1[1] - state["y"]) / (q1[0] - state["x"]), q1[0])
    step((nq2[1] - state["y"]) / (nq2[0] - state["x"]), nq2[0])
    return _unpack(state["f"])


def fq12_pow(base: "FQ12", exponent: int) -> "FQ12":
    """base^exponent by square-and-multiply, for exponents of thousands of bits.

    Works on packed integers, which is some twenty times faster than the
    library's own multiplication; the test suite checks it against the library.
    """
    b = _pack(ints(base))
    result = 1
    for bit in bin(exponent)[2:]:
        result = _mul_packed(result, result)
        if bit == "1":
            result = _mul_packed(result, b)
    return _unpack(result)


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
        cur_val = c.hint("root: square", fq12_mul(cur_val, cur_val))
        nxt = fq12_alloc(c, ints(cur_val))
        fq12_assert(c, [(1, cur, cur)], [(-1, nxt)], label="root: square")
        cur = nxt
        if bit == "1":
            cur_val = c.hint("root: multiply", fq12_mul(cur_val, root_val))
            nxt = fq12_alloc(c, ints(cur_val))
            fq12_assert(c, [(1, cur, root)], [(-1, nxt)], label="root: multiply")
            cur = nxt
    fq12_assert(c, [(1, cur, fq12_const(c, ints(denom))), (-1, acc.lin, fq12_const(c, ints(numer)))],
                label="pairing equation")


# ---------------------------------------------------------------- the folded final check

LAMBDA = ATE + P - P**2 + P**3


def loop_count(loop: str) -> int:
    """The number a loop string counts up to; the full loop gives 6x+2."""
    return int("1" + loop, 2)


@functools.lru_cache(maxsize=None)
def fold_numbers():
    """The number facts the folded check stands on, checked, and the fixed elements it uses.

    Write n = p^12 - 1 for the size of the multiplicative group, and h = n / r.

      * r divides lambda, so a lambda-th power is an r-th power.
      * gcd(lambda, n) = 3 r. Being an r-th power is therefore not quite enough
        to be a lambda-th power: a factor 3 is in the way.
      * 27 divides n exactly, and it divides h. Let omega have order 27. Then
        for every r-th power f exactly one of f, f * omega, f * omega^2 is a
        lambda-th power, and since omega^h = 1, multiplying by a power of omega
        never turns something that is not an r-th power into one.
    """
    n = P**12 - 1
    assert LAMBDA % R_ORDER == 0
    assert math.gcd(LAMBDA, n) == 3 * R_ORDER
    assert n % 27 == 0 and n % 81 and R_ORDER % 3 and n % (R_ORDER**2)
    assert (n // R_ORDER) % 27 == 0
    rest = n // (27 * R_ORDER)                # shares no factor with lambda, 27 or r
    assert math.gcd(rest, 3 * R_ORDER * LAMBDA) == 1
    t = 2
    while True:                               # any element that is not a cube gives order 27
        omega = fq12_pow(FQ12([t, 1] + [0] * 10), n // 27)
        if fq12_pow(omega, 9) != FQ12.one():
            break
        t += 1
    powers = [FQ12.one()]
    for _ in range(26):
        powers.append(powers[-1] * omega)
    assert powers[26] * omega == FQ12.one()
    return dict(n=n, rest=rest, omega=omega, powers=powers)


@functools.lru_cache(maxsize=None)
def frobenius_basis(e: int):
    """(w^i)^(p^e) for i = 0..11. Raising to p^e is linear over F_p, so these fix it."""
    image = _W
    for _ in range(e):
        image = fq12_pow(image, P)
    out = [FQ12.one()]
    for _ in range(11):
        out.append(out[-1] * image)
    return out


def frobenius_value(value: "FQ12", e: int) -> "FQ12":
    total = FQ12.zero()
    for coef, image in zip(ints(value), frobenius_basis(e)):
        total = total + image * coef
    return total


def residue_witness(f: "FQ12", exponent: int = LAMBDA):
    """(c, j) with c^exponent = f * omega^j, when one exists.

    For the real loop, exponent is lambda and a witness exists exactly when f
    becomes 1 under the final exponentiation. When none exists the values
    returned simply fail the gates.
    """
    numbers = fold_numbers()
    n, rest, powers = numbers["n"], numbers["rest"], numbers["powers"]
    if exponent % 3 or math.gcd(exponent // 3, 3 * rest) != 1:
        raise ValueError("this exponent does not have the shape of lambda")
    # The part of f inside the group of order 27, read off by trying all 27.
    part = fq12_pow(f, R_ORDER * rest)
    e = powers.index(part)
    in27 = e * pow(R_ORDER * rest, -1, 27) % 27         # f's part is omega^in27
    j = -in27 % 3
    y = f * powers[j]
    # Root in the part of the group that lambda does not touch, by inverting the exponent.
    lift = 27 * R_ORDER * pow(27 * R_ORDER, -1, rest)   # 1 modulo rest, 0 modulo 27 r
    c_rest = fq12_pow(y, lift * pow(exponent, -1, rest) % n)
    # Root in the group of order 27: omega^x with exponent * x = in27 + j.
    x = ((in27 + j) // 3) * pow(exponent // 3, -1, 9) % 9
    return c_rest * powers[x], j


def _frobenius_image(c: Circuit, element, value: "FQ12", e: int, label: str):
    """Fresh bits for value^(p^e), tied to `element` by twelve linear checks."""
    basis = frobenius_basis(e)
    image_val = c.hint(label, frobenius_value(value, e))
    image = fq12_alloc(c, ints(image_val))
    products = [(1, [element[i]] + [ZERO] * 11, fq12_const(c, ints(basis[i]))) for i in range(12)]
    fq12_assert(c, products, [(-1, image)], label=label)
    return image, image_val


def assert_pairing_folded(c: Circuit, pairs, numer: "FQ12", denom: "FQ12", loop: str = LOOP,
                          witness=None) -> None:
    """Assert that  Miller(pairs) * numer / denom  becomes 1 under the final exponentiation.

    The prover supplies c, its inverse d, and a choice j of 0, 1 or 2 with

        c^lambda = Miller(pairs) * numer / denom * omega^j .

    The gates check  c * d = 1,  run the pairing loop with d folded in, which
    yields  Miller(pairs) * d^(6x+2),  and then check

        that * d^p * c^(p^2) * d^(p^3)  =  denom / (numer * omega^j) .

    The right-hand side is one of three constants, picked by two bits.

    `loop` and `witness` are for small test instances, where the loop is cut
    short and the test supplies a matching (c, j) itself.
    """
    numbers = fold_numbers()
    if witness is None:
        miller = FQ12.one()
        for Q, Pt in pairs:
            miller = fq12_mul(miller, miller_value((Q.xv, Q.yv), (Pt.xv, Pt.yv), loop))
        witness = residue_witness(miller * numer / denom, loop_count(loop) + P - P**2 + P**3)
    root_val = c.hint("fold: root", witness[0])
    choice = c.hint("fold: choice", witness[1]) % 3
    inverse_val = c.hint("fold: inverse", FQ12.one() / root_val)
    root, inverse = fq12_alloc(c, ints(root_val)), fq12_alloc(c, ints(inverse_val))
    fq12_assert(c, [(1, root, inverse)], [(-1, fq12_const(c, ints(FQ12.one())))], label="fold: inverse")

    acc = miller_product(c, pairs, loop, fold=(inverse, inverse_val))

    f1, f1_val = _frobenius_image(c, inverse, inverse_val, 1, "fold: Frobenius 1")
    f2, f2_val = _frobenius_image(c, root, root_val, 2, "fold: Frobenius 2")
    f3, f3_val = _frobenius_image(c, inverse, inverse_val, 3, "fold: Frobenius 3")
    t1_val = c.hint("fold: first product", fq12_mul(acc.val, f1_val))
    t1 = fq12_alloc(c, ints(t1_val))
    fq12_assert(c, [(1, acc.lin, f1)], [(-1, t1)], label="fold: first product")
    t2_val = c.hint("fold: second product", fq12_mul(t1_val, f2_val))
    t2 = fq12_alloc(c, ints(t2_val))
    fq12_assert(c, [(1, t1, f2)], [(-1, t2)], label="fold: second product")

    # t2 * f3 must equal one of three constants; two bits, not both set, say which.
    targets = [denom / (numer * numbers["powers"][j]) for j in range(3)]
    flags = c.hint("fold: one choice", (int(choice == 1), int(choice == 2)))
    pick = [c.alloc(flag, 1) for flag in flags]
    c.gate([(1, pick[0], pick[1])], 0, [], "fold: one choice")
    as_element = lambda run: [Lin([(1, Big([Limb(run)], run.value))])] + [ZERO] * 11
    fq12_assert(
        c,
        [(1, t2, f3),
         (-1, as_element(pick[0]), fq12_const(c, ints(targets[1] - targets[0]))),
         (-1, as_element(pick[1]), fq12_const(c, ints(targets[2] - targets[0])))],
        [(-1, fq12_const(c, ints(targets[0])))],
        label="pairing equation")
