"""A builder for statements in the lock's format: gates over Boolean unknowns.

The lock accepts systems of gates

    sum of  <left, x> * <right, x>   =   <linear, x>            (mod r)

where x = (1, bits...) and r is the BN254 group order. This builder hands out
bits, records gates, and keeps the value of everything so it can tell whether
the assignment it was built with satisfies every gate. `satisfied` means
exactly that: every value fits its bits and every gate holds for those values.

Two modes:

  * `materialize=True` keeps the bits and the gates, ready to give to the lock.
    For small statements.
  * `materialize=False` only counts and checks. A proof-verifier statement has
    millions of bits; this mode lets it be built and checked without storing it.

Either way the builder keeps a fingerprint of the statement (the gates, not the
values), so two builds can be compared without storing either.

Every operand is a `Rng`: a run of consecutive bits read as an integer. A gate's
products are (coefficient, Rng, Rng) and its linear side is a constant plus
(coefficient, Rng) terms. `record` takes gates of any other shape, already
evaluated by whoever builds them; the wide arithmetic in `emulated.py` uses it.

`style` chooses how arithmetic modulo a foreign prime is laid out:

  * "wide"    few gates with many products each, and very little bookkeeping.
  * "narrow"  many gates with few products each, numbers cut into limbs.
"""
from __future__ import annotations

import hashlib

from ..field import Q as R_MOD
from ..relation import Gate


class Rng:
    """`width` consecutive bits starting at index `start`, holding `value`."""
    __slots__ = ("start", "width", "value", "_forms", "_spread")

    def __init__(self, start: int, width: int, value: int):
        self.start, self.width, self.value, self._forms, self._spread = start, width, value, None, None

    def spread(self) -> int:
        """The bits spaced out, one per 32-bit digit: sum of bit_t * 2^(32 t).

        Multiplying two of these as ordinary integers gives, in digit s, the
        number of pairs of set bits whose positions add up to s.
        """
        if self._spread is None:
            text = format(self.value, "b").replace("0", "00000000").replace("1", "00000001")
            self._spread = int(text, 16)
        return self._spread

    def form(self, coef: int = 1) -> dict:
        """coef * (this integer) as a linear form over the bits. Shared, read-only."""
        if self._forms is None:
            self._forms = {}
        if coef not in self._forms:
            self._forms[coef] = {self.start + t: (coef << t) % R_MOD for t in range(self.width)}
        return self._forms[coef]


class Circuit:
    def __init__(self, materialize: bool = False, limb_bits: int = 85, style: str = "wide"):
        if style not in ("wide", "narrow"):
            raise ValueError("style is 'wide' or 'narrow'")
        self.materialize = materialize
        self.limb_bits = limb_bits
        self.style = style
        self.n_bits = 0
        self.n_gates = 0
        self.n_products = 0
        self.max_products = 0
        self.satisfied = True
        self.failures = []
        self.x = [1] if materialize else None
        self.gates = [] if materialize else None
        self._shape = hashlib.sha256()   # running digest of the statement, without its values
        self.tamper = None          # tests only: see `hint`
        self._hints = {}
        self.stages = []            # (name, bits, gates, products) added since the last mark
        self._marked = (0, 0, 0)
        self._pairs = {}            # recent results of bit_pairs

    def alloc(self, value: int, width: int) -> Rng:
        """Hand out `width` fresh bits holding `value`."""
        if value < 0 or value >> width:
            self.satisfied = False
            self.failures.append("a value does not fit its bits")
            value &= (1 << width) - 1
        rng = Rng(self.n_bits + 1, width, value)
        self.n_bits += width
        if self.materialize:
            self.x.extend((value >> t) & 1 for t in range(width))
        return rng

    def record(self, lhs: int, rhs: int, n_products: int, shape, label: str = "", build=None) -> None:
        """Record one gate whose two sides have already been evaluated.

        lhs and rhs are the values of the product side and the linear side for
        the current assignment; the gate holds when they agree modulo r. This
        is the only place where an assignment is judged. `shape` describes the
        gate without its values, for the fingerprint. `build` returns the gate
        in the lock's own format and is called only when materialising.
        """
        if (lhs - rhs) % R_MOD:
            self.satisfied = False
            self.failures.append(label or f"gate {self.n_gates}")
        self._shape.update(repr(shape).encode())
        self.n_gates += 1
        self.n_products += n_products
        self.max_products = max(self.max_products, n_products)
        if self.materialize:
            self.gates.append(build())

    def gate(self, products, const: int, linear, label: str = "") -> None:
        """Record:  sum coef * a * b  =  const + sum coef * c   (mod r)."""
        def build():
            lin = {0: const % R_MOD}
            for coef, c in linear:
                for i, v in c.form(coef).items():
                    lin[i] = (lin.get(i, 0) + v) % R_MOD
            return Gate(left=tuple(a.form(coef) for coef, a, b in products),
                        right=tuple(b.form() for coef, a, b in products), linear=lin)

        self.record(
            lhs=sum(coef * a.value * b.value for coef, a, b in products),
            rhs=const + sum(coef * c.value for coef, c in linear),
            n_products=len(products),
            shape=([(coef, a.start, a.width, b.start, b.width) for coef, a, b in products],
                   const % R_MOD, [(coef, r.start, r.width) for coef, r in linear]),
            label=label, build=build)

    def bit_pairs(self, a: Rng, b: Rng) -> int:
        """In digit s: how many set bits of a and of b have positions adding up to s.

        One product of numbers is used by several gates in a row, so the most
        recent results are kept.
        """
        key = (a.start, b.start)
        found = self._pairs.get(key)
        if found is None:
            if len(self._pairs) >= 512:
                self._pairs.clear()
            found = self._pairs[key] = a.spread() * b.spread()
        return found

    def hint(self, label: str, value):
        """Pass through a value that the prover works out and a gate then checks.

        The gate that checks it carries the same label. Tests set `tamper`, a
        function (label, occurrence, value) -> value, to swap in a wrong value;
        everything computed afterwards uses the wrong value consistently, so
        the only thing standing in its way is the check with that label.
        """
        n = self._hints.get(label, 0)
        self._hints[label] = n + 1
        return value if self.tamper is None else self.tamper(label, n, value)

    def fingerprint(self) -> str:
        """A digest of the statement itself: which bits each gate touches, with
        which coefficients and constants. The values assigned to the bits do not
        enter. Two builds with the same fingerprint are the same statement."""
        return hashlib.sha256(self._shape.digest() + repr(self.n_bits).encode()).hexdigest()

    def mark(self, name: str) -> None:
        """Close a stage: record how much was added since the previous mark."""
        now = (self.n_bits, self.n_gates, self.n_products)
        self.stages.append((name,) + tuple(a - b for a, b in zip(now, self._marked)))
        self._marked = now

    def summary(self) -> dict:
        return dict(bits=self.n_bits, gates=self.n_gates, products=self.n_products,
                    max_products_per_gate=self.max_products, satisfied=self.satisfied)
