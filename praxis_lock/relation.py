"""Statements: systems of quadratic gates over Boolean unknowns.

`Gate`, `Relation`, `Sat` in Praxis/Algebra/Definitions/Relation.lean.

A solution is a vector x = (x_0, x_1, ..., x_N) with x_0 = 1 and every other
entry 0 or 1. A form is a sparse linear combination of the entries of x, written
as a dict {index: coefficient}; index 0 is the constant term. Each gate demands

    sum over products of  <left, x> * <right, x>   =   <linear, x>
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .field import Q
from .params import Params


@dataclass(frozen=True)
class Gate:
    left: tuple = ()
    right: tuple = ()
    linear: dict = None

    def __post_init__(self):
        if len(self.left) != len(self.right):
            raise ValueError("a gate needs as many left forms as right forms")
        if self.linear is None:
            object.__setattr__(self, "linear", {})


def dot(form: dict, x) -> int:
    return sum(c * x[i] for i, c in form.items()) % Q


def pad(P: Params, gates) -> tuple:
    """A statement always has exactly `gates_max` gates; unused ones say 0 = 0."""
    gates = tuple(gates)
    if len(gates) > P.gates_max:
        raise ValueError("too many gates for these parameters")
    return gates + (Gate(),) * (P.gates_max - len(gates))


def in_shape(P: Params, gates) -> bool:
    forms = [f for g in gates for f in (*g.left, *g.right, g.linear)]
    return (len(gates) == P.gates_max
            and all(len(g.left) <= P.products_max for g in gates)
            and all(0 <= i <= P.N for f in forms for i in f))


def is_witness(P: Params, gates, x) -> bool:
    if len(x) != P.N + 1 or x[0] != 1 or any(v not in (0, 1) for v in x[1:]):
        return False
    return all(
        sum(dot(l, x) * dot(r, x) for l, r in zip(g.left, g.right)) % Q == dot(g.linear, x)
        for g in gates
    )


def digest(gates) -> bytes:
    """A fingerprint of a statement, used to derive its public QAP points."""
    h = hashlib.sha256()
    for g in gates:
        h.update(b"gate")
        for forms in (g.left, g.right, (g.linear,)):
            h.update(len(forms).to_bytes(4, "big"))
            for form in forms:
                for i in sorted(form):
                    if form[i] % Q:
                        h.update(i.to_bytes(4, "big") + (form[i] % Q).to_bytes(32, "big"))
                h.update(b";")
    return h.digest()


# ---------------------------------------------------------------- common gates

def and_gate(a: int, b: int, c: int) -> Gate:
    """x_a AND x_b = x_c."""
    return Gate(({a: 1},), ({b: 1},), {c: 1})


def xor_gate(a: int, b: int, c: int) -> Gate:
    """x_a XOR x_b = x_c, written as 2 x_a x_b = x_a + x_b - x_c."""
    return Gate(({a: 2},), ({b: 1},), {a: 1, b: 1, c: -1})


def and_equals(a: int, b: int, value: int) -> Gate:
    """x_a AND x_b = value (0 or 1)."""
    return Gate(({a: 1},), ({b: 1},), {0: value})


def xor_equals(a: int, b: int, value: int) -> Gate:
    """x_a XOR x_b = value (0 or 1)."""
    return Gate(({a: 2},), ({b: 1},), {a: 1, b: 1, 0: -value})


def bit_equals(a: int, value: int) -> Gate:
    """x_a = value (0 or 1)."""
    return Gate((), (), {a: 1, 0: -value})


def product_equals(a_bits, b_bits, target: int) -> Gate:
    """(the number with bits a_bits) * (the number with bits b_bits) = target.

    Bits are listed lowest first, as indices into x. One gate, one product: the
    statement "I know a factorisation of target".
    """
    return Gate(({i: 1 << k for k, i in enumerate(a_bits)},),
                ({i: 1 << k for k, i in enumerate(b_bits)},),
                {0: target})


def number_bits(value: int, width: int):
    return [(value >> k) & 1 for k in range(width)]
