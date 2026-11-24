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
    worked out again here for these gates. Two further forms, the compact one
    (`assert_pairing_compact`) and the bundled one (`assert_pairing_bundled`),
    build on the folded one and are described where they are defined, near
    the end of this file.

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


def miller_value(q2, p1, loop=LOOP) -> "FQ12":
    """The same value as `miller_reference`, computed the way the gates compute it.

    Used by the prover, which needs the Miller values before it can choose its
    root. The test suite checks it against the library's loop. `loop` is a
    string of bits or a list of signed digits (0, 1, -1); a digit -1 adds -Q.
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
        if int(bit):
            step((qy * int(bit) - state["y"]) / (qx - state["x"]), qx)
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


# ---------------------------------------------------------------- the compact check
#
# Three further savings on top of the folded check.
#
# Half-size running product. The final exponentiation sends every element of
# the subfield F_p^6 to 1, because p^6 - 1 divides (p^12 - 1) / r. So the check
# never needs the running product itself, only its class up to factors from
# that subfield. Write an element as a + b w with a, b in F_p^6 (w^2 = v lies
# in F_p^6); its class is the single F_p^6 number g = a / b, six numbers of
# the base field where the element itself takes twelve. Classes multiply by
#
#     (g1 + w)(g2 + w) = (g1 + g2) (g3 + w)   with   g3 (g1 + g2) = g1 g2 + v,
#
# one equation of degree two. Since v is not a square in F_p^6, that equation
# has no solution with g1 + g2 = 0, so it always means what it says.
#
# Merged lines. Each loop step multiplies in one line per pairing. The line of
# the pairing whose G2 point is fixed, divided by -y of its G1 point, is
# 1 + b w with b a fixed combination of two numbers stored once (x/y and 1/y).
# The other pairing's line is l0 + a w with a made of stored numbers. Their
# product has class tau with  tau (l0 b + a) = l0 + v a b,  again degree two,
# so both lines enter the running product through one stored class.
#
# Signed loop. The loop count 6x+2 is written with digits 0, 1, -1, which has
# 21 additions where binary has 36. A digit -1 adds -Q and multiplies by c
# where a digit 1 multiplies by 1/c; the result differs from the binary loop's
# only by factors from F_p^6.
#
# The three-way choice of the folded check disappears: omega lies in F_p^6.

def signed_digits(n: int):
    """n in digits 0, 1, -1 with no two neighbours non-zero, most significant first."""
    out = []
    while n:
        d = (2 - n % 4) if n & 1 else 0
        out.append(d)
        n = (n - d) >> 1
    return out[::-1]


SIGNED_LOOP = signed_digits(ATE)[1:]
assert signed_digits(ATE)[0] == 1


def signed_count(digits) -> int:
    """The number a list of signed digits counts up to, after a leading 1."""
    n = 1
    for d in digits:
        n = 2 * n + d
    return n


_V = _W * _W


def _split(f: "FQ12"):
    """f = a + b w with a, b in F_p^6 (the even powers of w)."""
    co = ints(f)
    return (FQ12([co[i] if i % 2 == 0 else 0 for i in range(12)]),
            FQ12([co[i + 1] if i % 2 == 0 else 0 for i in range(12)]))


def fq6_inverse(x: "FQ12") -> "FQ12":
    """1/x for x in F_p^6, by solving the six linear equations  x * y = 1.

    Much faster than the library's general inverse; the tests compare the two.
    """
    co = ints(x)[0::2]
    # Column j of the matrix is x * v^j in the basis 1, v, ..., v^5, where v^6 = 18 v^3 - 82.
    cols, cur = [], list(co)
    for _ in range(6):
        cols.append(cur)
        top = cur[5]
        cur = [(-82 * top) % P, cur[0], cur[1], (cur[2] + 18 * top) % P, cur[3], cur[4]]
    rows = [[cols[j][i] for j in range(6)] + [1 if i == 0 else 0] for i in range(6)]
    for i in range(6):
        pivot = next(r for r in range(i, 6) if rows[r][i])
        rows[i], rows[pivot] = rows[pivot], rows[i]
        scale = pow(rows[i][i], -1, P)
        rows[i] = [v * scale % P for v in rows[i]]
        for r in range(6):
            if r != i and rows[r][i]:
                f = rows[r][i]
                rows[r] = [(a - f * b) % P for a, b in zip(rows[r], rows[i])]
    out = [0] * 12
    out[0::2] = [rows[i][6] for i in range(6)]
    return FQ12(out)


def torus_class(f: "FQ12") -> "FQ12":
    """The class of f up to factors from F_p^6: a / b for f = a + b w."""
    a, b = _split(f)
    return fq12_mul(a, fq6_inverse(b))


def torus_mul(g1: "FQ12", g2: "FQ12") -> "FQ12":
    """The class of a product, from the classes of its factors."""
    return fq12_mul(fq12_mul(g1, g2) + _V, fq6_inverse(g1 + g2))


@functools.lru_cache(maxsize=None)
def _torus_frobenius_basis(e: int, sign: int):
    """The class of (sign*g + w)^(p^e) is linear in g: the images of 1, w^2, ..., w^10."""
    zeta = fq12_pow(_W, P**e - 1)                      # w^(p^e) = zeta * w
    assert _split(zeta)[1] == FQ12.zero()               # zeta lies in F_p^6
    scale = fq6_inverse(zeta) * sign
    return [fq12_mul(frobenius_basis(e)[i], scale) for i in range(0, 12, 2)]


def _fq6_alloc(c: Circuit, value: "FQ12"):
    """Fresh bits for an element of F_p^6: six numbers, at the even powers of w."""
    co = ints(value)
    return [fp_alloc(c, co[i]) if i % 2 == 0 else ZERO for i in range(12)]


def _times_v(el):
    """v times an element of F_p^6, where v = w^2 and w^12 = 18 w^6 - 82."""
    out = [ZERO] * 12
    for k in range(0, 10, 2):
        out[k + 2] = out[k + 2] + el[k]
    out[6] = out[6] + el[10].scale(18)
    out[0] = out[0] + el[10].scale(-82)
    return out


def _times(c: Circuit, lin: Lin, k: int, label: str = "compact: constant multiple") -> Lin:
    """k * lin for a constant k of any size. Free in the wide layout, where
    constants are reduced before anything is summed; a stored number in the narrow one."""
    k %= P
    if c.style == "wide":
        return lin.scale(k)
    value = c.hint(label, lin.value() * k % P)
    out = fp_alloc(c, value)
    fp_assert(c, [(1, lin, fp_const(c, k))], [(-1, out)], label=label)
    return out


def _t_mul(c: Circuit, g1, v1, g2, v2, label: str):
    """Fresh bits for the class of a product; one equation of degree two ties it down."""
    value = c.hint(label, torus_mul(v1, v2))
    out = _fq6_alloc(c, value)
    fq12_assert(c, [(1, out, g1), (1, out, g2), (-1, g1, g2)],
                [(-1, fq12_const(c, ints(_V)))], label=label)
    return out, value


def _t_frobenius(c: Circuit, g, value: "FQ12", e: int, sign: int, label: str):
    """The class of (sign*g + w)^(p^e), as a fixed linear image of g's six numbers."""
    basis = _torus_frobenius_basis(e, sign)
    image_val = FQ12.zero()
    for coef, image in zip(ints(value)[0::2], basis):
        image_val = image_val + image * coef
    if c.style == "wide":
        out = [ZERO] * 12
        for i, image in enumerate(basis):
            for k, coef in enumerate(ints(image)):
                if coef:
                    out[k] = out[k] + g[2 * i].scale(coef)
        return out, image_val
    image_val = c.hint(label, image_val)
    out = _fq6_alloc(c, image_val)
    products = [(1, [g[2 * i]] + [ZERO] * 11, fq12_const(c, ints(basis[i]))) for i in range(6)]
    fq12_assert(c, products, [(-1, out)], label=label)
    return out, image_val


def _fixed_double(R):
    """Tangent at a fixed twist point: slope, intercept, and the doubled point. No gates."""
    x, y = R
    m = 3 * x * x / (2 * y)
    nx = m * m - 2 * x
    return m, y - m * x, (nx, m * (x - nx) - y)


def _fixed_add(R, Q):
    """Chord through two fixed twist points: slope, intercept, and the sum. No gates."""
    (x, y), (qx, qy) = R, Q
    m = (qy - y) / (qx - x)
    nx = m * m - x - qx
    return m, y - m * x, (nx, m * (x - nx) - y)


def _sqrt_in_fq2(value: int) -> "FQ2":
    """A square root, in F_p^2, of a number of F_p. One always exists: p = 3 mod 4,
    so either the number or its negative is a square in F_p, and u^2 = -1."""
    value %= P
    s = pow(value, (P + 1) // 4, P)
    if s * s % P == value:
        return FQ2([s, 0])
    return FQ2([0, pow(-value % P, (P + 1) // 4, P)])


def _fq2_times(c: Circuit, pair, k: "FQ2"):
    """A constant of F_p^2 times an F_p^2 value made of bits."""
    k0, k1 = ints(k)
    part = lambda lin, coef: _times(c, lin, coef) if lin and coef % P else ZERO
    return (part(pair[0], k0) + part(pair[1], -k1), part(pair[0], k1) + part(pair[1], k0))


class _Rescaled:
    """The moving G2 point followed without its y-coordinate and without separate line values.

    Let A = (x_A, y_A) be the G1 point of this pairing and alpha in F_p^2 with
    alpha^2 = 1 / x_A. Move the twist curve by  X = x / x_A,  Y = y * alpha^3.
    It keeps its shape, slopes become mu = alpha * m and intercepts nu = alpha^3 * t,
    and the line through a point, evaluated at A and multiplied by alpha^3
    (a factor from F_p^2, which the check ignores), is

        l0 + E(mu) w + E(nu) w^3        with  l0 = -alpha^3 y_A  stored once.

    So the slope and intercept that the point arithmetic needs anyway are the
    line's values, and nothing more is stored for the line.

    The y-coordinate is never stored either. After a step with line (mu, nu)
    that lands on X', the new point is (X', -(mu X' + nu)); the next step's
    line (mu', nu') must pass through it, which is  (mu' + mu) X' + nu' + nu = 0.
    A tangent is recognised by  2 X' X + X^2 + 2 mu nu = 0  together with
    X' = mu^2 - 2 X; with the point on the line this is  2 mu Y = 3 X^2.
    Every equation has degree two. Stored per step: mu, nu and X', six numbers.
    """

    def __init__(self, c: Circuit, Q: G2Point, Pt: G1Point):
        self.c = c
        self.x_a = (Pt.x, ZERO)
        beta_v = c.hint("rescaled: one over x", pow(Pt.xv, -1, P))
        beta = fp_alloc(c, beta_v)
        fp_assert(c, [(1, beta, Pt.x)], [(-1, fp_const(c, 1))], label="rescaled: one over x")
        alpha_v = c.hint("rescaled: root of that", _sqrt_in_fq2(beta_v))
        self.alpha = fq2_alloc(c, ints(alpha_v))
        fq2_assert(c, [(1, self.alpha, self.alpha)], [(-1, (beta, ZERO))], label="rescaled: root of that")
        self.beta_v, self.cube_v = beta_v, alpha_v * beta_v            # alpha^3 = alpha / x_A
        self.l0_v = c.hint("rescaled: constant term", FQ2.zero() - self.cube_v * Pt.yv)
        self.l0 = fq2_alloc(c, ints(self.l0_v))
        fq2_assert(c, [(1, self.l0, self.x_a), (1, self.alpha, (Pt.y, ZERO))], [],
                   label="rescaled: constant term")                    # l0 x_A = -alpha y_A
        self.start = self.scaled(Q)
        self.X, self.Y, self.Xv, self.Yv = self.start
        self.prev = None

    def scaled(self, pt: G2Point):
        """Fresh bits for a twist point's coordinates on the rescaled curve."""
        c = self.c
        xv = c.hint("rescaled: point x", pt.xv * self.beta_v)
        yv = c.hint("rescaled: point y", pt.yv * self.cube_v)
        x, y = fq2_alloc(c, ints(xv)), fq2_alloc(c, ints(yv))
        fq2_assert(c, [(1, x, self.x_a)], [(-1, pt.x)], label="rescaled: point x")          # X x_A = x
        fq2_assert(c, [(1, y, self.x_a), (-1, pt.y, self.alpha)], [], label="rescaled: point y")  # Y x_A = y alpha
        return x, y, xv, yv

    def _on_line(self, mu, nu):
        if self.Y is not None:                    # the starting point, whose y is stored
            fq2_assert(self.c, [(1, mu, self.X)], [(1, nu), (-1, self.Y)], label="rescaled: on the line")
        else:                                     # y = -(previous slope * X + previous intercept)
            pm, pn = self.prev
            fq2_assert(self.c, [(1, (mu[0] + pm[0], mu[1] + pm[1]), self.X)], [(1, nu), (1, pn)],
                       label="rescaled: on the line")

    def _line(self, mu, nu, mu_v, nu_v):
        line = [ZERO] * 12
        for pos, value in ((0, self.l0), (1, mu), (3, nu)):
            line[pos] = value[0] - value[1].scale(9)
            line[pos + 6] = value[1]
        (a0, a1), (b0, b1), (c0, c1) = ints(self.l0_v), ints(mu_v), ints(nu_v)
        return line, FQ12([(a0 - 9 * a1) % P, (b0 - 9 * b1) % P, 0, (c0 - 9 * c1) % P, 0, 0,
                           a1, b1, 0, c1, 0, 0])

    def _advance(self, mu, nu, nx, mu_v, nu_v, nx_v):
        self.X, self.Y, self.Xv, self.Yv = nx, None, nx_v, FQ2.zero() - (mu_v * nx_v + nu_v)
        self.prev = (mu, nu)
        return self._line(mu, nu, mu_v, nu_v)

    def double(self):
        """The tangent line at the current point; the point becomes its double."""
        c = self.c
        mu_v = c.hint("rescaled: tangent", 3 * self.Xv * self.Xv / (2 * self.Yv))
        nu_v = c.hint("rescaled: on the line", self.Yv - mu_v * self.Xv)
        nx_v = c.hint("rescaled: new x", mu_v * mu_v - 2 * self.Xv)
        mu, nu, nx = (fq2_alloc(c, ints(v)) for v in (mu_v, nu_v, nx_v))
        self._on_line(mu, nu)
        fq2_assert(c, [(2, nx, self.X), (1, self.X, self.X), (2, mu, nu)], [], label="rescaled: tangent")
        fq2_assert(c, [(1, mu, mu)], [(-2, self.X), (-1, nx)], label="rescaled: new x")
        return self._advance(mu, nu, nx, mu_v, nu_v, nx_v)

    def add(self, other, sign: int = 1, want_sum: bool = True):
        """The chord through the current point and `other` (from `scaled`), or its negative."""
        c = self.c
        ox, oy, oxv, oyv = other
        if sign == -1:
            oy, oyv = (-oy[0], -oy[1]), FQ2.zero() - oyv
        mu_v = c.hint("rescaled: through the other point", (oyv - self.Yv) / (oxv - self.Xv))
        nu_v = c.hint("rescaled: on the line", self.Yv - mu_v * self.Xv)
        mu, nu = fq2_alloc(c, ints(mu_v)), fq2_alloc(c, ints(nu_v))
        self._on_line(mu, nu)
        fq2_assert(c, [(1, mu, ox)], [(1, nu), (-1, oy)], label="rescaled: through the other point")
        if not want_sum:
            return self._line(mu, nu, mu_v, nu_v)
        nx_v = c.hint("rescaled: new x", mu_v * mu_v - self.Xv - oxv)
        nx = fq2_alloc(c, ints(nx_v))
        fq2_assert(c, [(1, mu, mu)], [(-1, self.X), (-1, ox), (-1, nx)], label="rescaled: new x")
        return self._advance(mu, nu, nx, mu_v, nu_v, nx_v)


def _compact_tail(c: Circuit, g, g_val, start, start_val, numer: "FQ12", denom: "FQ12") -> None:
    """The end of the compact check. c^(-lambda) still lacks (1/c)^p * c^(p^2) * (1/c)^(p^3):
    three linear images of the root's class. Then the result must be the class of denom / numer."""
    for e, sign, label in ((1, 1, "compact: Frobenius 1"), (2, -1, "compact: Frobenius 2")):
        image, image_val = _t_frobenius(c, start, start_val, e, sign, label)
        g, g_val = _t_mul(c, g, g_val, image, image_val, label + " product")
    image, _ = _t_frobenius(c, start, start_val, 3, 1, "compact: Frobenius 3")
    target = fq12_const(c, ints(torus_class(denom / numer)))
    fq12_assert(c, [(1, target, g), (1, target, image), (-1, g, image)],
                [(-1, fq12_const(c, ints(_V)))], label="pairing equation")


def assert_pairing_compact(c: Circuit, moving, fixed, numer: "FQ12", denom: "FQ12",
                           loop=SIGNED_LOOP, witness=None, rescale: bool = False) -> None:
    """Assert that  Miller(moving) * Miller(fixed) * numer / denom  becomes 1 under the
    final exponentiation, storing as little as this package knows how to.

    `moving` is (Q, P) with Q a G2 point made of bits; `fixed` is (Q, P) with Q a
    constant G2 point. Both P are G1 points made of bits.

    The prover supplies the class g of 1/c, where c^lambda = Miller(...) * numer / denom
    up to a factor from F_p^6. Nothing ties g down on its own: any g for which
    the last equation holds proves the claim, because the gates then show that
    the product equals (g + w)^(-lambda) times an element of F_p^6.

    With `rescale`, the moving G2 point is followed on a rescaled curve, without
    its y-coordinate and without separate line values (see `_Rescaled`).

    `loop` and `witness` are for small test instances.
    """
    (Q, Pt), (Qf, Ptf) = moving, fixed
    if Q.const or not Qf.const:
        raise ValueError("the compact check takes one G2 point made of bits and one constant")
    if witness is None:
        product = fq12_mul(miller_value((Q.xv, Q.yv), (Pt.xv, Pt.yv), loop),
                           miller_value((Qf.xv, Qf.yv), (Ptf.xv, Ptf.yv), loop))
        witness = residue_witness(product * numer / denom, signed_count(loop) + P - P**2 + P**3)
    root_val = c.hint("compact: root", witness[0])
    g_val = torus_class(FQ12.one() / root_val)
    start = _fq6_alloc(c, g_val)
    start_val = g_val

    # Two numbers of the fixed pairing's G1 point, and their products with y of the other.
    def stored(label, value, products, linears):
        value = c.hint(label, value % P)
        out = fp_alloc(c, value)
        fp_assert(c, [(k, out if a is None else a, out if b is None else b) for k, a, b in products],
                  [(k, out if a is None else a) for k, a in linears], label=label)
        return out

    y_inv = pow(Ptf.yv, -1, P)
    rho = stored("compact: x over y", Ptf.xv * y_inv, [(1, None, Ptf.y)], [(-1, Ptf.x)])
    eta = stored("compact: one over y", y_inv, [(1, None, Ptf.y)], [(-1, fp_const(c, 1))])

    # The moving line is l0 + a w. Its constant term l0, an F_p^2 number, times rho and eta:
    if rescale:
        mover = _Rescaled(c, Q, Pt)
        l0_rho, l0_eta = [], []
        for out, scalar, label in ((l0_rho, rho, "rescaled: constant term times x over y"),
                                   (l0_eta, eta, "rescaled: constant term times one over y")):
            value = c.hint(label, mover.l0_v * scalar.value())
            out.extend(fq2_alloc(c, ints(value)))
            fq2_assert(c, [(1, mover.l0, (scalar, ZERO))], [(-1, tuple(out))], label=label)
    else:                                   # l0 = -y of the moving pairing's G1 point
        lam1 = stored("compact: y times x over y", Pt.yv * rho.value(), [(1, Pt.y, rho)], [(-1, None)])
        lam2 = stored("compact: y times one over y", Pt.yv * eta.value(), [(1, Pt.y, eta)], [(-1, None)])
        l0_rho, l0_eta = (-lam1, ZERO), (-lam2, ZERO)

    def step(g, g_val, line, lv, m, t):
        """Multiply both pairings' lines into the running class through one stored class."""
        (m0, m1), (t0, t1) = ints(m), ints(t)
        b, b_co = [ZERO] * 12, [0] * 12
        for pos, k in ((0, m0 - 9 * m1), (6, m1), (2, t0 - 9 * t1), (8, t1)):
            source = rho if pos in (0, 6) else eta
            b[pos] = _times(c, source, -k)                 # the fixed line over -y is 1 + b w
            b_co[pos] = -k * source.value() % P
        # l0 * b = -E(m * l0 rho) - E(t * l0 eta) v, with E(r0 + r1 u) = (r0 - 9 r1) + r1 w^6
        l0b = [ZERO] * 12
        for pos, (r0, r1) in ((0, _fq2_times(c, l0_rho, m)), (2, _fq2_times(c, l0_eta, t))):
            l0b[pos] = r1.scale(9) - r0
            l0b[pos + 6] = -r1
        bv = FQ12(b_co)
        l0v, av = _split(lv)
        a = [line[k + 1] if k % 2 == 0 else ZERO for k in range(12)]
        tau_val = c.hint("compact: lines", fq12_mul(l0v + fq12_mul(_V, fq12_mul(av, bv)),
                                                    fq6_inverse(fq12_mul(l0v, bv) + av)))
        tau = _fq6_alloc(c, tau_val)
        fq12_assert(c, [(1, tau, [l0b[i] + a[i] for i in range(12)]), (-1, _times_v(a), b)],
                    [(-1, [line[k] if k % 2 == 0 else ZERO for k in range(12)])], label="compact: lines")
        return _t_mul(c, g, g_val, tau, tau_val, "compact: step")

    negated = lambda pt: G2Point(pt.x, (-pt.y[0], -pt.y[1]), pt.xv, -pt.yv, pt.const)
    conj = lambda v: FQ2([int(v.coeffs[0]), (-int(v.coeffs[1])) % P])
    g, R, Rf = start, Q, (Qf.xv, Qf.yv)
    for d in loop:
        g, g_val = _t_mul(c, g, g_val, g, g_val, "compact: square")
        if rescale:
            line, lv = mover.double()
        else:
            line, lv, R = _double_step(c, R, Pt)
        m, t, Rf = _fixed_double(Rf)
        g, g_val = step(g, g_val, line, lv, m, t)
        if d:
            # digit 1: one more factor 1/c, class g0.  digit -1: one more factor c, class -g0.
            fold = (start, start_val) if d == 1 else ([-x for x in start], FQ12.zero() - start_val)
            g, g_val = _t_mul(c, g, g_val, fold[0], fold[1], "compact: fold")
            if rescale:
                line, lv = mover.add(mover.start, sign=d)
            else:
                line, lv, R = _add_step(c, R, Q if d == 1 else negated(Q), Pt)
            m, t, Rf = _fixed_add(Rf, (Qf.xv, Qf.yv * d))
            g, g_val = step(g, g_val, line, lv, m, t)
    q1, nq2 = _frobenius_points(c, Q)
    f1 = (conj(Qf.xv) * FROB_X, conj(Qf.yv) * FROB_Y)
    f2 = (Qf.xv * FROB2_X, -(Qf.yv * FROB2_Y))
    if rescale:
        line, lv = mover.add(mover.scaled(q1))
    else:
        line, lv, R = _add_step(c, R, q1, Pt)
    m, t, Rf = _fixed_add(Rf, f1)
    g, g_val = step(g, g_val, line, lv, m, t)
    if rescale:
        line, lv = mover.add(mover.scaled(nq2), want_sum=False)
    else:
        line, lv, _ = _add_step(c, R, nq2, Pt, want_sum=False)
    m, t, _ = _fixed_add(Rf, f2)
    g, g_val = step(g, g_val, line, lv, m, t)

    _compact_tail(c, g, g_val, start, start_val, numer, denom)


# ---------------------------------------------------------------- the bundled check
#
# The rescaled compact check stores three values of six numbers for every loop
# step: the running class squared, the class of the step's two lines, and their
# product. Two changes bring that down.
#
# Two forms. Let l0 be the constant term of the moving pairing's line, an F_p^2
# number stored once. Up to factors from F_p^6, an element is kept in one of
#
#     form A:  G + l0 w          form B:  l0 + h w          (G, h in F_p^6)
#
# The moving line is l0 + a w with `a` made of stored numbers, so it is in
# form B as it stands and nothing is stored for it. With L = l0^2:
#
#     square of A:   G' * 2G        = G^2 + L v        gives form A
#     square of B:   G' * 2h        = L + h^2 v        gives form A
#     A times B:     h' * (G + h v) = G h + L          gives form B
#     B times B:     G' * (h1 + h2) = L + h1 h2 v      gives form A
#     A times A:     G' * (G1 + G2) = G1 G2 + L v      gives form A
#
# Each equation has degree two. Each ties down its new value: the factor next
# to it cannot be zero while the equation holds, because v is not a square in
# F_p^6 and l0 is not zero. So a doubling step is "square, then times the
# line", two stored values where there were three.
#
# Bundled fixed lines. The other pairing's line, divided by -y of its G1 point
# C, is  1 + (A rho + B eta) w  with rho = x_C / y_C and eta = 1 / y_C stored
# once and A, B constants. Over a few steps in a row these lines, with the
# powers of two they pick up from the squarings in between, multiply to a
# fixed polynomial in rho and eta. C lies on the curve, so rho^3 = eta - 3 eta^3
# and a polynomial of degree d needs only the 3d monomials rho^b eta^c with
# b <= 2, which are stored once. A bundle is then N + M w with N and M fixed
# combinations of stored numbers; its form-A value G, with  G * M = l0 * N,
# is stored and enters the running value by one of the rules above. That is
# two stored values per bundle where there was one per step.
#
# At the end the running value is turned back into a plain class (g l0 = G, or
# g h = l0) and the compact check's closing lines finish the job.

BUNDLE_DEGREE = 23       # the largest degree of a bundle; 17 bundles for the full loop


def bundle_plan(loop, max_degree: int = BUNDLE_DEGREE):
    """Split the loop's steps into runs whose fixed lines form one bundle each.

    Each step brings one fixed line, two if it has an addition; the last step
    also takes the two closing lines. A line that enters j steps before the end
    of its run is squared j times, so it adds 2^j to the bundle's degree. Runs
    are grown greedily while the degree stays within `max_degree`.
    Returns a list of (steps, degree).
    """
    runs, steps, degree = [], [], 0
    for k, d in enumerate(loop):
        lines = 1 + (1 if d else 0) + (2 if k == len(loop) - 1 else 0)
        if steps and 2 * degree + lines > max_degree:
            runs.append((steps, degree))
            steps, degree = [], 0
        degree = 2 * degree + lines
        steps.append(k)
    runs.append((steps, degree))
    return runs


def _poly_times(a: dict, b: dict) -> dict:
    """Product of two polynomials in rho and eta whose coefficients are packed
    elements of F_p^12, reduced with rho^3 = eta - 3 eta^3."""
    acc = {}

    def put(key, z, k):
        row = acc.get(key)
        if row is None:
            row = acc[key] = [0] * 12
        for i in range(12):
            row[i] += k * ((z >> (_SLOT * i)) & _SLOT_MASK)

    for (b1, c1), x in a.items():
        for (b2, c2), y in b.items():
            z = _mul_packed(x, y)
            eb, ec = b1 + b2, c1 + c2
            if eb < 3:
                put((eb, ec), z, 1)
            else:
                put((eb - 3, ec + 1), z, 1)
                put((eb - 3, ec + 3), z, -3)
    return {key: _pack(v % P for v in row) for key, row in acc.items() if any(v % P for v in row)}


def _fixed_line_poly(m: "FQ2", t: "FQ2") -> dict:
    """The fixed line with slope m and intercept t, over -y of its G1 point:
    1 - E(m) rho w - E(t) eta w^3, as a polynomial in rho and eta."""
    (m0, m1), (t0, t1) = ints(m), ints(t)
    by_rho, by_eta = [0] * 12, [0] * 12
    by_rho[1], by_rho[7] = -(m0 - 9 * m1) % P, -m1 % P
    by_eta[3], by_eta[9] = -(t0 - 9 * t1) % P, -t1 % P
    return {(0, 0): 1, (1, 0): _pack(by_rho), (0, 1): _pack(by_eta)}


@functools.lru_cache(maxsize=8)
def _bundles(qf, loop, max_degree: int):
    """The bundles of fixed lines for a fixed G2 point, as polynomials in rho and eta.

    Depends only on the fixed point and the loop, never on a proof. Returns a
    list of (last step, {(b, c): twelve coefficients}) for the monomials rho^b eta^c.
    """
    conj = lambda v: FQ2([int(v.coeffs[0]), (-int(v.coeffs[1])) % P])
    qx, qy = FQ2(list(qf[0])), FQ2(list(qf[1]))
    R, lines = (qx, qy), []
    for d in loop:
        m, t, R = _fixed_double(R)
        here = [_fixed_line_poly(m, t)]
        if d:
            m, t, R = _fixed_add(R, (qx, qy * d))
            here.append(_fixed_line_poly(m, t))
        lines.append(here)
    m, t, R = _fixed_add(R, (conj(qx) * FROB_X, conj(qy) * FROB_Y))
    lines[-1].append(_fixed_line_poly(m, t))
    m, t, _ = _fixed_add(R, (qx * FROB2_X, -(qy * FROB2_Y)))
    lines[-1].append(_fixed_line_poly(m, t))

    out = []
    for steps, degree in bundle_plan(loop, max_degree):
        poly = {(0, 0): 1}
        for k in steps:
            poly = _poly_times(poly, poly)
            for line in lines[k]:
                poly = _poly_times(poly, line)
        assert max(b + c for b, c in poly) <= degree
        out.append((steps[-1], {key: tuple((v >> (_SLOT * i)) & _SLOT_MASK for i in range(12))
                                for key, v in poly.items()}))
    return out


def _split_bundle(poly: dict):
    """A bundle as N + M w: for each monomial, its six coefficients in N (even degree) or in M (odd).

    A product of k line terms is an element of F_p^6 times w^k, so monomials of
    even degree have coefficients in F_p^6 and those of odd degree in F_p^6 w.
    Anything else is refused.
    """
    even, odd = {}, {}
    for key, coefs in poly.items():
        shift = (key[0] + key[1]) % 2
        if any(coefs[1 - shift::2]):
            raise ValueError("not a product of lines: a coefficient on the wrong side")
        (odd if shift else even)[key] = tuple(coefs[shift::2])
    return even, odd


def _embed_value(v: "FQ2") -> "FQ12":
    a0, a1 = ints(v)
    return FQ12([(a0 - 9 * a1) % P, 0, 0, 0, 0, 0, a1, 0, 0, 0, 0, 0])


class _Forms:
    """The two forms of a running value and the five rules between them (see above)."""

    def __init__(self, c: Circuit, l0, l0_v: "FQ2"):
        self.c = c
        self.l0 = [ZERO] * 12
        self.l0[0], self.l0[6] = l0[0] - l0[1].scale(9), l0[1]
        self.l0_v = _embed_value(l0_v)
        # L = l0^2. The root alpha is a plain or a purely imaginary number, so L lies in F_p.
        square_v = c.hint("bundled: constant term squared", int((l0_v * l0_v).coeffs[0]))
        square = fp_alloc(c, square_v)
        fq2_assert(c, [(1, l0, l0)], [(-1, (square, ZERO))], label="bundled: constant term squared")
        self.L = [square] + [ZERO] * 11
        self.L_v = FQ12([square_v % P] + [0] * 11)
        self.Lv = _times_v(self.L)
        self.Lv_v = fq12_mul(self.L_v, _V)

    def _new(self, label: str, top: "FQ12", bottom: "FQ12"):
        value = self.c.hint(label, fq12_mul(top, fq6_inverse(bottom)))
        return _fq6_alloc(self.c, value), value

    def square(self, x, xv, form: str, label: str):
        """The square of a value in either form, in form A."""
        if form == "A":
            out, value = self._new(label, fq12_mul(xv, xv) + self.Lv_v, xv + xv)
            fq12_assert(self.c, [(2, out, x), (-1, x, x)], [(-1, self.Lv)], label=label)
        else:
            out, value = self._new(label, self.L_v + fq12_mul(fq12_mul(xv, xv), _V), xv + xv)
            fq12_assert(self.c, [(2, out, x), (-1, _times_v(x), x)], [(-1, self.L)], label=label)
        return out, value

    def a_times_b(self, g, gv, h, hv, label: str):
        """Form A times form B, in form B."""
        out, value = self._new(label, fq12_mul(gv, hv) + self.L_v, gv + fq12_mul(hv, _V))
        fq12_assert(self.c, [(1, out, g), (1, out, _times_v(h)), (-1, h, g)], [(-1, self.L)], label=label)
        return out, value

    def b_times_b(self, h1, h1v, h2, h2v, label: str):
        """Form B times form B, in form A."""
        out, value = self._new(label, self.L_v + fq12_mul(fq12_mul(h1v, h2v), _V), h1v + h2v)
        fq12_assert(self.c, [(1, out, h1), (1, out, h2), (-1, _times_v(h1), h2)], [(-1, self.L)],
                    label=label)
        return out, value

    def a_times_a(self, g1, g1v, g2, g2v, label: str):
        """Form A times form A, in form A."""
        out, value = self._new(label, fq12_mul(g1v, g2v) + self.Lv_v, g1v + g2v)
        fq12_assert(self.c, [(1, out, g1), (1, out, g2), (-1, g1, g2)], [(-1, self.Lv)], label=label)
        return out, value

    def times_b(self, x, xv, form: str, h, hv, label: str):
        """A running value in either form times a form-B value. Returns (bits, value, form)."""
        if form == "A":
            return self.a_times_b(x, xv, h, hv, label) + ("B",)
        return self.b_times_b(x, xv, h, hv, label) + ("A",)

    def times_a(self, x, xv, form: str, g, gv, label: str):
        """A running value in either form times a form-A value. Returns (bits, value, form)."""
        if form == "A":
            return self.a_times_a(x, xv, g, gv, label) + ("A",)
        return self.a_times_b(g, gv, x, xv, label) + ("B",)

    def scaled(self, g, gv, label: str):
        """The form-A value of a plain class g: G = l0 g."""
        value = self.c.hint(label, fq12_mul(self.l0_v, gv))
        out = _fq6_alloc(self.c, value)
        fq12_assert(self.c, [(1, self.l0, g)], [(-1, out)], label=label)
        return out, value

    def plain(self, x, xv, form: str, label: str):
        """The plain class a/b of a running value: g l0 = G in form A, g h = l0 in form B."""
        if form == "A":
            out, value = self._new(label, xv, self.l0_v)
            fq12_assert(self.c, [(1, self.l0, out)], [(-1, x)], label=label)
        else:
            out, value = self._new(label, self.l0_v, xv)
            fq12_assert(self.c, [(1, out, x)], [(-1, self.l0)], label=label)
        return out, value


def assert_pairing_bundled(c: Circuit, moving, fixed, numer: "FQ12", denom: "FQ12",
                           loop=SIGNED_LOOP, witness=None, max_degree: int = BUNDLE_DEGREE) -> None:
    """Assert the same thing as `assert_pairing_compact` with `rescale`, storing less.

    The running value is kept in two forms so that the moving line needs no
    stored class of its own, and the fixed pairing's lines enter in bundles of
    a few steps (see the comment above). `max_degree` bounds the size of a
    bundle; `loop` and `witness` are for small test instances.
    """
    (Q, Pt), (Qf, Ptf) = moving, fixed
    if Q.const or not Qf.const:
        raise ValueError("the bundled check takes one G2 point made of bits and one constant")
    loop = tuple(loop)
    if witness is None:
        product = fq12_mul(miller_value((Q.xv, Q.yv), (Pt.xv, Pt.yv), loop),
                           miller_value((Qf.xv, Qf.yv), (Ptf.xv, Ptf.yv), loop))
        witness = residue_witness(product * numer / denom, signed_count(loop) + P - P**2 + P**3)
    root_val = c.hint("bundled: root", witness[0])
    start_val = torus_class(FQ12.one() / root_val)
    start = _fq6_alloc(c, start_val)                      # the class of 1/c, six numbers

    mover = _Rescaled(c, Q, Pt)
    forms = _Forms(c, mover.l0, mover.l0_v)
    root, root_v = forms.scaled(start, start_val, "bundled: root, scaled")

    # Two numbers of the fixed pairing's G1 point, and the monomials in them that the bundles use.
    def stored(label, value, a, b, equals):
        """A fresh number t with  t * b = equals  (a is None)  or  a * b = t  (equals is None)."""
        value = c.hint(label, value % P)
        out = fp_alloc(c, value)
        fp_assert(c, [(1, out if a is None else a, b)], [(-1, out if equals is None else equals)], label=label)
        return out, value

    y_inv = pow(Ptf.yv, -1, P)
    rho, rho_v = stored("compact: x over y", Ptf.xv * y_inv, None, Ptf.y, Ptf.x)        # rho y = x
    eta, eta_v = stored("compact: one over y", y_inv, None, Ptf.y, fp_const(c, 1))      # eta y = 1
    bundles = _bundles((tuple(ints(Qf.xv)), tuple(ints(Qf.yv))), loop, max_degree)
    needed = set()
    for _, poly in bundles:
        for b, e in poly:
            while (b, e) not in needed and (b, e) not in ((0, 0), (1, 0), (0, 1)):
                needed.add((b, e))
                b, e = (1, e) if b == 2 else (b, e - 1)       # what it is made from
    mono = {(0, 0): (None, 1), (1, 0): (rho, rho_v), (0, 1): (eta, eta_v)}
    for b, e in sorted(needed, key=lambda key: (key[0] + key[1], key[0])):
        below, by = (mono[(1, e)], (rho, rho_v)) if b == 2 else (mono[(b, e - 1)], (eta, eta_v))
        mono[(b, e)] = stored("bundled: power", below[1] * by[1], below[0], by[0], None)

    def bundle(poly):
        """The form-A value G of a bundle of fixed lines N + M w:  G * M = l0 * N."""
        sides = []
        for part in _split_bundle(poly):
            lins, values = [ZERO] * 12, [0] * 12
            for key, coefs in sorted(part.items()):
                lin, value = mono[key]
                for i, k in enumerate(coefs):
                    if k:
                        lins[2 * i] = lins[2 * i] + (fp_const(c, k) if lin is None else _times(c, lin, k))
                        values[2 * i] = (values[2 * i] + k * value) % P
            sides.append((lins, FQ12(values)))
        (n, n_v), (m, m_v) = sides
        value = c.hint("bundled: fixed lines", fq12_mul(fq12_mul(forms.l0_v, n_v), fq6_inverse(m_v)))
        out = _fq6_alloc(c, value)
        fq12_assert(c, [(1, out, m), (-1, forms.l0, n)], [], label="bundled: fixed lines")
        return out, value

    def b_part(line, lv):
        """The moving line l0 + a w is in form B as it stands: its a."""
        return [line[k + 1] if k % 2 == 0 else ZERO for k in range(12)], _split(lv)[1]

    ends = {last: poly for last, poly in bundles}
    x, xv, form = root, root_v, "A"
    for k, d in enumerate(loop):
        x, xv = forms.square(x, xv, form, "bundled: square")
        h, hv = b_part(*mover.double())
        x, xv, form = forms.times_b(x, xv, "A", h, hv, "bundled: line")
        if d:
            # digit 1: one more factor 1/c.  digit -1: one more factor c, whose class is the negative.
            fold = (root, root_v) if d == 1 else ([-t for t in root], FQ12.zero() - root_v)
            x, xv, form = forms.times_a(x, xv, form, fold[0], fold[1], "bundled: root in")
            h, hv = b_part(*mover.add(mover.start, sign=d))
            x, xv, form = forms.times_b(x, xv, form, h, hv, "bundled: line")
        if k in ends and k != len(loop) - 1:
            g, gv = bundle(ends[k])
            x, xv, form = forms.times_a(x, xv, form, g, gv, "bundled: fixed lines in")
    q1, nq2 = _frobenius_points(c, Q)
    h, hv = b_part(*mover.add(mover.scaled(q1)))
    x, xv, form = forms.times_b(x, xv, form, h, hv, "bundled: line")
    h, hv = b_part(*mover.add(mover.scaled(nq2), want_sum=False))
    x, xv, form = forms.times_b(x, xv, form, h, hv, "bundled: line")
    g, gv = bundle(ends[len(loop) - 1])
    x, xv, form = forms.times_a(x, xv, form, g, gv, "bundled: fixed lines in")

    g, g_val = forms.plain(x, xv, form, "bundled: class")
    _compact_tail(c, g, g_val, start, start_val, numer, denom)
