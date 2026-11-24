"""Turn "I hold a valid proof" into a statement the lock understands.

Run with:  python demo_encoding.py      (needs `pip install py_ecc`)

Part 1 locks a secret to a statement about BN254 arithmetic, written with the
encoding's building blocks, and opens it. Part 2 makes a real Groth16 proof,
writes the Groth16 verifier as lock gates, and shows that the real proof
satisfies them while forged ones do not. Part 3 builds the same verifier the
earlier ways this package knows, to show what each shrinking step saved.

Research code at toy parameters. The verifier statement is far too large for
this Python lock to encrypt to; parts 2 and 3 build and check it, and report
its size next to the size the paper achieves.
"""
import random
import secrets
import time

from praxis_lock import ExponentGroups, Params, decrypt, encrypt
from praxis_lock.encoding import Circuit, P, fp_alloc, fp_assert, fp_const
from praxis_lock.encoding.tolock import lock_statement


def part_one() -> None:
    print("PART 1  A statement about BN254 numbers, through the lock\n")
    root = random.Random(7).randrange(P)
    target = root * root % P

    def build(candidate: int) -> Circuit:
        c = Circuit(materialize=True)
        y = fp_alloc(c, candidate)
        fp_assert(c, [(1, y, y)], [(-1, fp_const(c, target))])      # y * y = target  (mod p)
        return c

    honest = build(root)
    params, statement, solution = lock_statement(honest)
    print(f"Statement: 'I know a number whose square is T modulo the BN254 prime'")
    print(f"           written as {honest.n_gates} gates over {honest.n_bits} bits "
          f"(a 254-bit number does not fit in one field element of the lock)")

    G = ExponentGroups()
    secret = secrets.token_bytes(32)
    start = time.time()
    ct = encrypt(G, params, statement, secret)
    print(f"LOCK   on the {G.name} backend, which hides nothing, in {time.time() - start:.1f} s")
    start = time.time()
    opened = decrypt(G, params, statement, ct, solution)
    print(f"OPEN   the square root opens it: {opened == secret}  ({time.time() - start:.1f} s)")
    wrong = build((root + 1) % P)
    print(f"WRONG  a different number satisfies the gates? {wrong.satisfied}."
          f"  Decryption returns: {decrypt(G, params, statement, ct, wrong.x)}")
    assert opened == secret


def part_two() -> None:
    try:
        from py_ecc import bn128 as bn
    except ImportError:
        print("\nPART 2 needs the pairing library:  pip install py_ecc")
        return
    from praxis_lock.encoding import groth16 as g

    print("\nPART 2  The Groth16 verifier as a lock statement\n")
    r1cs = g.factoring_r1cs()
    vk, trapdoor = g.setup(r1cs)
    proof = g.prove(r1cs, trapdoor, [1, 211 * 223, 211, 223, 211 * 211])
    print("Proof:     a real Groth16 proof of 'I know two numbers whose product is 47,053'")
    print(f"           the ordinary verifier accepts it: {g.verify(vk, [47053], proof)}")

    start = time.time()
    c = g.encode_verifier(vk, [47053], proof)
    took = time.time() - start
    print(f"\nGATES  the same check written for the lock, built in {took:.1f} s:")
    for name, bits, gates, _ in c.stages:
        print(f"       {bits:>10,} bits  {gates:>7,} gates   {name}")
    print(f"       {c.n_bits:>10,} bits  {c.n_gates:>7,} gates   in total,"
          f" at most {c.max_products} products in one gate")
    print(f"       the real proof satisfies every gate: {c.satisfied}")

    A, B, C = proof
    forged = (A, B, bn.multiply(bn.G1, 12345))
    print(f"\nFORGED a proof with its third point replaced satisfies them:"
          f" {g.encode_verifier(vk, [47053], forged).satisfied}")
    print(f"       the real proof, claimed for 47,054 instead, satisfies them:"
          f" {g.encode_verifier(vk, [47054], proof).satisfied}")
    assert c.satisfied

    paper = Params.paper()
    print("\nPART 3  What each shrinking step saved\n")
    print("       arithmetic   final check             unknown bits     gates   widest gate   times the paper")
    names = {"root": "separate root", "folded": "folded into loop", "compact": "compact (default)"}
    for style, check in (("narrow", "root"), ("wide", "root"), ("wide", "folded"), ("wide", "compact")):
        other = c if (style, check) == ("wide", "compact") else g.encode_verifier(vk, [47053], proof, style=style, check=check)
        assert other.satisfied
        print(f"       {style:<10}   {names[check]:<21} {other.n_bits:>14,} {other.n_gates:>9,} {other.max_products:>13,}"
              f" {other.n_bits / paper.N:>17.1f}")
    print(f"       the paper's own encoding         {paper.N:>14,} {1692:>9,}  at most 2,047 {1.0:>17.1f}")
    print("\nThe paper does not say how its encoding is built. This one was written from")
    print("scratch, is still nearly twice the size, and has known gaps; see the README.")


if __name__ == "__main__":
    part_one()
    part_two()
