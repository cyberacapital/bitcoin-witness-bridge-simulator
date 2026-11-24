"""The three groups G1, G2, GT and the pairing between them.

All three are written additively, as in the Lean development (`BilinearGroups` in
Praxis/Setting.lean): `encT(a)` is a times the generator of GT, and pairing
`enc1(a)` with `enc2(b)` gives `encT(a b)`.

Two backends:

  * `ExponentGroups` is Lean's `expGroups`: an element IS its exponent. It hides
    nothing, so it is not encryption. It exists to run and test the algebra fast.
  * `BN254Groups` uses the real BN254 pairing through the `py_ecc` library. This
    is the setting the paper instantiates. It is slow in pure Python.
"""
from __future__ import annotations

from .field import Q


class ExponentGroups:
    """Elements are their own exponents. NOT hiding: for testing the algebra only."""

    name = "exponents (no secrecy)"
    hiding = False

    zero1 = zero2 = zeroT = 0

    def enc1(self, v):
        return v % Q

    enc2 = encT = enc1

    def add1(self, a, b):
        return (a + b) % Q

    add2 = addT = add1

    def mul1(self, k, a):
        return k * a % Q

    mul2 = mulT = mul1

    def pair(self, a, b):
        return a * b % Q

    def pair_sum(self, pairs):
        return sum(a * b for a, b in pairs) % Q

    def eqT(self, a, b):
        return (a - b) % Q == 0

    def serT(self, a) -> bytes:
        return (a % Q).to_bytes(32, "big")


class BN254Groups:
    """Real pairing groups on the BN254 curve, via py_ecc."""

    name = "BN254 pairing"
    hiding = True

    def __init__(self):
        try:
            from py_ecc import optimized_bn128 as bn
        except ImportError as exc:  # pragma: no cover
            raise ImportError("BN254Groups needs the py_ecc package: pip install py_ecc") from exc
        if bn.curve_order != Q:
            raise RuntimeError("py_ecc curve order does not match the scheme's q")
        self.bn = bn
        self.zero1, self.zero2, self.zeroT = bn.Z1, bn.Z2, bn.FQ12.one()
        self._gT = bn.pairing(bn.G2, bn.G1)

    def enc1(self, v):
        return self.bn.multiply(self.bn.G1, v % Q)

    def enc2(self, v):
        return self.bn.multiply(self.bn.G2, v % Q)

    def encT(self, v):
        return self._gT ** (v % Q)

    def add1(self, a, b):
        return self.bn.add(a, b)

    add2 = add1

    def addT(self, a, b):
        return a * b

    def mul1(self, k, a):
        return self.bn.multiply(a, k % Q)

    mul2 = mul1

    def mulT(self, k, a):
        return a ** (k % Q)

    def pair(self, a, b):
        return self.bn.pairing(b, a)

    def pair_sum(self, pairs):
        """Sum of pairings, sharing one final exponentiation."""
        acc = self.bn.FQ12.one()
        for a, b in pairs:
            if self.bn.is_inf(a) or self.bn.is_inf(b):
                continue
            acc = acc * self.bn.pairing(b, a, final_exponentiate=False)
        return self.bn.final_exponentiate(acc)

    def eqT(self, a, b):
        return a == b

    def serT(self, a) -> bytes:
        return b"".join(int(c).to_bytes(32, "big") for c in a.coeffs)
