"""The quadratic arithmetic program that turns a statement into polynomials.

`QAPData`, `ValidQAP`, `HonestQuotient` in Praxis/QAP.lean and `RowPoints.toQAP`
in Praxis/Domain.lean.

Every unknown i gets two polynomials A_i and B_i, fixed by their values on D
public points:

  * one point per unknown, where A(x) B(x) = (x_j - x_0) x_j, forcing x_j to be a bit;
  * the point rho, where A(x) B(x) = x_0^2 = 1. The key hides here;
  * U points per gate, on a coset of the U-th roots of unity. There A(x) B(x)
    is Z0 times one term of the gate, so the gate holds exactly when those terms
    sum to zero over the coset.

Lean obtains the honest quotient polynomials by an existence argument. Here
they are computed, by the interpolation and division described in
`QAP.honest_quotient`.
"""
from __future__ import annotations

from .field import (Q, hash_to_field, interpolate, inv, lagrange_basis_at, pcoeff, pdivmod,
                    peval, pmul, poly_from_roots, psub)
from .params import Params, _prime_factors
from .relation import in_shape


def _root_of_unity(order: int, c0: int) -> int:
    mu = pow(c0, (Q - 1) // order, Q)
    if pow(mu, order, Q) != 1 or any(pow(mu, order // p, Q) == 1 for p in _prime_factors(order)):
        raise ValueError("could not find a root of unity of the required order")
    return mu


class QAP:
    def __init__(self, P: Params, gates):
        if not in_shape(P, gates):
            raise ValueError("statement does not fit these parameters")
        self.P, self.gates = P, tuple(gates)
        self._choose_points()
        self._tabulate()

    # -- RowPoints: an admissible choice of evaluation points, fixed by the parameters

    def _choose_points(self) -> None:
        P = self.P
        tag = ("praxis-lock/points", P.N, P.gates_max, P.U)
        self.mu = _root_of_unity(P.U, P.c0)
        counter = 0
        while True:
            col = [hash_to_field(*tag, "col", j, counter) for j in range(P.N + 1)]
            rho = hash_to_field(*tag, "rho", counter)
            delta = [hash_to_field(*tag, "delta", t, counter) for t in range(P.gates_max)]
            gate = [[d * pow(self.mu, j, Q) % Q for j in range(P.U)] for d in delta]
            xs = col + [rho] + [p for row in gate for p in row]
            cosets = {pow(d, P.U, Q) for d in delta}
            if len(set(xs)) == P.D and len(cosets) == P.gates_max and 0 not in delta:
                break
            counter += 1
        self.col, self.rho, self.delta, self.gate_points, self.xs = col, rho, delta, gate, xs
        self.rho_index = P.N + 1
        self.gate_index = lambda t, j: P.N + 2 + t * P.U + j

    @staticmethod
    def place(j: int):
        """Which term of a gate sits at the j-th point of its coset: the linear
        term at j = 0, product j - 1 otherwise."""
        return None if j == 0 else j - 1

    # -- the values that define A_i, B_i and L_rho (aVal, bVal)

    def _tabulate(self) -> None:
        P = self.P
        self.z0 = pmul([(-self.rho) % Q, 1], poly_from_roots(self.col))
        self.z = poly_from_roots([x for k, x in enumerate(self.xs) if k != self.rho_index])
        self.a_vals = [[0] * P.D for _ in range(P.N + 1)]
        self.b_vals = [[0] * P.D for _ in range(P.N + 1)]
        for i in range(P.N + 1):
            for j in range(1, P.N + 1):
                self.a_vals[i][j] = 1 if i == j else (Q - 1 if i == 0 else 0)
            self.b_vals[i][i] = 1
        self.a_vals[0][self.rho_index] = 1
        self.b_vals[0][self.rho_index] = 1
        for t, g in enumerate(self.gates):
            for j in range(P.U):
                k = self.gate_index(t, j)
                z0d = peval(self.z0, self.xs[k])
                f = self.place(j)
                if f is None:
                    self.a_vals[0][k] = z0d
                    for i, c in g.linear.items():
                        self.b_vals[i][k] = (-c) % Q
                elif f < len(g.left):
                    for i, c in g.left[f].items():
                        self.a_vals[i][k] = z0d * c % Q
                    for i, c in g.right[f].items():
                        self.b_vals[i][k] = c % Q

    # -- evaluation at the secret point tau (encryption)

    def eval_at(self, tau: int):
        """A_i(tau), B_i(tau) for every i, then L_rho(tau), Z(tau), Z0(tau)."""
        basis = lagrange_basis_at(self.xs, tau)
        a = [sum(v * b for v, b in zip(row, basis) if v) % Q for row in self.a_vals]
        b = [sum(v * b for v, b in zip(row, basis) if v) % Q for row in self.b_vals]
        return a, b, basis[self.rho_index], peval(self.z, tau), peval(self.z0, tau)

    # -- the honest quotient (decryption)

    def honest_quotient(self, x):
        """Polynomials (hk, ho, R) with

            A(x) B(x) - L_rho = hk * Z * (X - rho) + Z0 * R
            A(x) B(x)         = ho * Z             + Z0 * R

        where R has degree below U * gates and no terms X^k with U dividing k.
        Returns None if x does not solve the statement.

        R is forced: on every gate point it must equal A(x) B(x) / Z0, and its
        degree is below the number of gate points, so interpolate it there. The
        gate equations are exactly what make its forbidden coefficients vanish.
        """
        P = self.P
        a_at = [sum(x[i] * self.a_vals[i][k] for i in range(P.N + 1)) % Q for k in range(P.D)]
        b_at = [sum(x[i] * self.b_vals[i][k] for i in range(P.N + 1)) % Q for k in range(P.D)]
        product = pmul(interpolate(self.xs, a_at), interpolate(self.xs, b_at))

        first_gate = P.N + 2
        gate_xs = self.xs[first_gate:]
        r_at = [a_at[k] * b_at[k] % Q * inv(peval(self.z0, self.xs[k])) % Q
                for k in range(first_gate, P.D)]
        R = interpolate(gate_xs, r_at)
        if any(pcoeff(R, k) for k in range(0, P.U * P.gates_max, P.U)):
            return None

        rest = psub(product, pmul(self.z0, R))
        ho, rem_o = pdivmod(rest, self.z)
        l_rho = interpolate(self.xs, [1 if k == self.rho_index else 0 for k in range(P.D)])
        hk, rem_k = pdivmod(psub(rest, l_rho), pmul(self.z, [(-self.rho) % Q, 1]))
        if rem_o or rem_k:
            return None
        return hk, ho, R
