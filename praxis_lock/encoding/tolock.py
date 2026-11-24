"""Hand a materialised circuit to the lock: pick parameters that fit it."""
from __future__ import annotations

from ..params import Params
from ..relation import pad
from .circuit import Circuit


def lock_params(circuit: Circuit, g: int = 7) -> Params:
    """Toy lock parameters sized for this circuit. No security."""
    u = 2
    while u < circuit.max_products + 1:
        u *= 2
    return Params.toy(N=circuit.n_bits, gates=circuit.n_gates, U=u, n=8, d=4, K1=2, g=g)


def lock_statement(circuit: Circuit):
    """(parameters, statement, solution) for a circuit built with materialize=True."""
    if not circuit.materialize:
        raise ValueError("build the circuit with materialize=True to use it with the lock")
    P = lock_params(circuit)
    return P, pad(P, circuit.gates), list(circuit.x)
