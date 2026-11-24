"""Parameters of the scheme: `Params` and `Params.Valid` in Praxis/Params.lean.

`Params.toy` builds small parameter sets that satisfy every structural condition
the scheme needs, so the algebra can be run and tested. They offer no security.
`Params.paper` holds the paper's 100-bit parameters, used here only to check that
this code's element counts reproduce the published ciphertext size.
"""
from __future__ import annotations

from dataclasses import dataclass

from .field import Q


class ParamError(ValueError):
    pass


def _prime_factors(n: int):
    out, p = [], 2
    while p * p <= n:
        if n % p == 0:
            out.append(p)
            while n % p == 0:
                n //= p
        p += 1
    if n > 1:
        out.append(n)
    return out


@dataclass(frozen=True)
class Params:
    N: int          # number of Boolean unknowns in a statement
    gates_max: int  # number of gates a statement may have
    U: int          # each gate holds up to U - 1 products
    D: int          # number of QAP points
    Mq: int
    Wr: int
    E1: int
    E1p: int
    n: int          # degree of the extension field F_q[X] / (X^n - c0)
    m: int
    c0: int
    beta: int       # a primitive n-th root of unity
    d: int          # degree of the Module-SIS ring F_q[X] / (X^d + 1)
    rows: int
    cols: int
    K1: int
    K2: int
    g: int          # width of the grid the search sample is drawn from
    t_bound: int    # the decryption search covers [-t_bound, t_bound]
    message_bytes: int = 32
    seed_bytes: int = 32
    q: int = Q

    @property
    def products_max(self) -> int:
        return self.U - 1

    # -- Params.Valid, BetaLaws and KummerIrreducible

    def validate(self) -> "Params":
        def need(cond, what):
            if not cond:
                raise ParamError(what)

        q = self.q
        need(min(self.N, self.gates_max, self.U, self.n, self.d, self.rows, self.cols,
                 self.K1, self.K2, self.Mq, self.Wr, self.g) > 0, "all sizes must be positive")
        need(self.n == 2 * self.m, "n = 2m")
        need((q - 1) % self.n == 0, "n divides q - 1")
        need(self.n == self.d * (self.rows + 1), "n = d (rows + 1)")
        need(self.N <= self.cols * self.d < self.N + self.d, "N <= cols d < N + d")
        need(self.K1 * self.K2 == self.m, "K1 K2 = m")
        need(self.D == self.N + 2 + self.U * self.gates_max, "D = N + 2 + U gates")
        need(self.D <= self.E1 * self.Mq, "D <= E1 Mq")
        need(self.E1p * self.Wr == self.gates_max, "E1' Wr = gates")
        need((q - 1) % self.U == 0, "U divides q - 1")
        need(self.g % 2 == 1, "g is odd")
        need(self.t_bound == (self.N + 1) * (self.g + 1), "t_bound = (N + 1)(g + 1)")
        need(2 * self.t_bound + 1 < q, "2 t_bound + 1 < q")
        # BetaLaws
        need(self.beta == pow(self.c0, (q - 1) // self.n, q), "beta = c0^((q-1)/n)")
        need(pow(self.beta, self.m, q) == q - 1, "beta^m = -1")
        need(pow(self.beta, self.n, q) == 1, "beta^n = 1")
        for p in _prime_factors(self.n):
            need(pow(self.beta, self.n // p, q) != 1, "beta has order exactly n")
            # X^n - c0 is irreducible when c0 is not a p-th power for any prime p | n
            # (and q = 1 mod 4 when 4 | n, which holds for BN254).
            need(pow(self.c0, (q - 1) // p, q) != 1, "X^n - c0 is irreducible")
        need(self.n % 4 != 0 or q % 4 == 1, "X^n - c0 is irreducible")
        # The decryption-side equalities need K1 + 1 < q and enough field for the points.
        need(self.D < q and self.K1 + 1 < q, "field is large enough")
        return self

    # -- constructors

    @classmethod
    def toy(cls, N: int = 4, gates: int = 3, U: int = 2, n: int = 8, d: int = 2,
            K1: int = 2, g: int = 7, Mq: int = 2, c0: int = 5) -> "Params":
        m = n // 2
        D = N + 2 + U * gates
        return cls(
            N=N, gates_max=gates, U=U, D=D, Mq=Mq, Wr=gates, E1=-(-D // Mq), E1p=1,
            n=n, m=m, c0=c0, beta=pow(c0, (Q - 1) // n, Q), d=d, rows=n // d - 1,
            cols=-(-N // d), K1=K1, K2=m // K1, g=g, t_bound=(N + 1) * (g + 1),
        ).validate()

    @classmethod
    def paper(cls) -> "Params":
        """The 100-bit instantiation (`Params.main`). Far too large to run here."""
        return cls(
            N=391693, gates_max=1694, U=2048, D=3861007, Mq=11, Wr=11, E1=351001, E1p=154,
            n=4608, m=2304, c0=5,
            beta=15455056031545522072329890326622223210121953105925913337013641664433101999706,
            d=64, rows=71, cols=6121, K1=192, K2=12, g=1736375024658487,
            t_bound=680127678908581798672,
        )

    # -- sizes (`countG1`, `countG2` in Praxis/Published.lean)

    @property
    def leg_count(self) -> int:
        return 3 * self.K2

    @property
    def cell_count(self) -> int:
        return self.K1 * self.leg_count

    def count_g1(self) -> int:
        return (2 * self.K1 * (self.N + 1) + 2 * self.K1 + self.E1
                + (self.U - 1) * self.E1p + (self.N + 1))

    def count_g2(self) -> int:
        return (2 * 2 * self.leg_count * (self.N + 1) + 2 * 2 * self.leg_count
                + 2 * self.cell_count * self.Mq + 2 * self.cell_count * self.Wr
                + 2 * self.leg_count + 2 * 5 * (2 * self.n - 1) + 2 * 3 * (self.n - 1))

    def ciphertext_bytes(self) -> int:
        """Size with compressed BN254 points: 32 bytes in G1, 64 in G2, 384 in GT."""
        return 32 * self.count_g1() + 64 * self.count_g2() + 384 + self.seed_bytes + self.message_bytes
