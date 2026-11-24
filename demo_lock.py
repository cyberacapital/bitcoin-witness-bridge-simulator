"""Lock a secret to a factoring puzzle with the Praxis scheme, then open it.

Run with:  python demo_lock.py            (real BN254 pairings; needs `pip install py_ecc`)
           python demo_lock.py --fast     (exponent backend: instant, hides nothing)

Toy parameters only. Two 8-bit factors can be found by trying every pair, so
this protects nothing; it shows the machinery working end to end.
"""
import sys
import time

from praxis_lock import (BN254Groups, ExponentGroups, Params, decrypt, encrypt, is_witness,
                         number_bits, pad, product_equals)

TARGET = 211 * 223  # 47,053


def solution(a: int, b: int):
    return [1] + number_bits(a, 8) + number_bits(b, 8)


def main() -> None:
    fast = "--fast" in sys.argv
    if not fast:
        try:
            G = BN254Groups()
        except ImportError:
            print("py_ecc is not installed, so using the fast backend (no secrecy).")
            print("For real pairings:  pip install py_ecc\n")
            fast = True
    if fast:
        G = ExponentGroups()

    P = Params.toy(N=16, gates=1, U=2, n=8, d=2, K1=2, g=7)
    statement = pad(P, [product_equals(range(1, 9), range(9, 17), TARGET)])
    secret = b"a key piece for a Bitcoin vault."

    print(f"Groups:    {G.name}")
    print(f"Statement: find two 8-bit numbers whose product is {TARGET:,}\n")

    start = time.time()
    ct = encrypt(G, P, statement, secret)
    n1, n2 = ct.counts()
    print(f"LOCK   {n1} elements of G1 and {n2} of G2, about {P.ciphertext_bytes() / 1024:.0f} KB,"
          f" in {time.time() - start:.1f} s")
    print(f"       masked secret in the ciphertext: {ct.cm.hex()[:32]}...")

    wrong = solution(211, 222)
    print(f"\nWRONG  211 x 222 solves the statement? {is_witness(P, statement, wrong)}."
          f"  Decryption returns: {decrypt(G, P, statement, ct, wrong)}")

    start = time.time()
    opened = decrypt(G, P, statement, ct, solution(211, 223))
    print(f"OPEN   211 x 223 solves it, and decryption returns: {opened!r}"
          f"  ({time.time() - start:.1f} s)")
    assert opened == secret

    paper = Params.paper()
    print(f"\nAt the paper's 100-bit parameters a ciphertext holds"
          f" {paper.count_g1():,} + {paper.count_g2():,} elements,")
    print(f"about {paper.ciphertext_bytes() / 2**30:.2f} GiB, for statements with"
          f" {paper.N:,} unknown bits. This code cannot run at that size.")


if __name__ == "__main__":
    main()
