"""Dependency-free cryptography helpers for the bridge simulator.

The maths is real (secp256k1, Schnorr-style signatures, SHA-256, Merkle trees)
but written for clarity. It is not constant-time and must never protect real keys.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


# ---------------------------------------------------------------- hashing

def _enc(part) -> bytes:
    if isinstance(part, (bytes, bytearray)):
        return b"b" + bytes(part)
    if isinstance(part, str):
        return b"s" + part.encode()
    if isinstance(part, bool):
        raise TypeError("refusing to hash a bool")
    if isinstance(part, int):
        return b"i" + part.to_bytes(33, "big")
    if isinstance(part, (list, tuple)):
        return b"l" + sha(*part)
    raise TypeError(f"cannot hash {type(part).__name__}")


def sha(*parts) -> bytes:
    """SHA-256 over a typed, length-prefixed encoding of the parts."""
    h = hashlib.sha256()
    for part in parts:
        data = _enc(part)
        h.update(len(data).to_bytes(4, "big"))
        h.update(data)
    return h.digest()


# ---------------------------------------------------------------- secp256k1

def point_add(a, b):
    """Affine point addition. None is the point at infinity."""
    if a is None:
        return b
    if b is None:
        return a
    (x1, y1), (x2, y2) = a, b
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if a == b:
        lam = 3 * x1 * x1 * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return (x3, (lam * (x1 - x3) - y1) % P)


def _jac_double(x1, y1, z1):
    if y1 == 0 or z1 == 0:
        return (0, 1, 0)
    yy = y1 * y1 % P
    s = 4 * x1 * yy % P
    m = 3 * x1 * x1 % P
    x3 = (m * m - 2 * s) % P
    y3 = (m * (s - x3) - 8 * yy * yy) % P
    z3 = 2 * y1 * z1 % P
    return (x3, y3, z3)


def _jac_add(p, q):
    x1, y1, z1 = p
    x2, y2, z2 = q
    if z1 == 0:
        return q
    if z2 == 0:
        return p
    z1z1 = z1 * z1 % P
    z2z2 = z2 * z2 % P
    u1 = x1 * z2z2 % P
    u2 = x2 * z1z1 % P
    s1 = y1 * z2 * z2z2 % P
    s2 = y2 * z1 * z1z1 % P
    if u1 == u2:
        if s1 != s2:
            return (0, 1, 0)
        return _jac_double(x1, y1, z1)
    h = (u2 - u1) % P
    r = (s2 - s1) % P
    h2 = h * h % P
    h3 = h * h2 % P
    u1h2 = u1 * h2 % P
    x3 = (r * r - h3 - 2 * u1h2) % P
    y3 = (r * (u1h2 - x3) - s1 * h3) % P
    z3 = h * z1 * z2 % P
    return (x3, y3, z3)


def point_mul(k: int, pt=G):
    """Scalar multiplication k * pt, returned as an affine point."""
    k %= N
    if k == 0 or pt is None:
        return None
    acc = (0, 1, 0)
    addend = (pt[0], pt[1], 1)
    while k:
        if k & 1:
            acc = _jac_add(acc, addend)
        addend = _jac_double(*addend)
        k >>= 1
    x, y, z = acc
    if z == 0:
        return None
    zi = pow(z, -1, P)
    zi2 = zi * zi % P
    return (x * zi2 % P, y * zi2 * zi % P)


def ser_point(pt) -> bytes:
    if pt is None:
        return b"\x00" * 64
    return pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


def deser_point(data: bytes):
    if len(data) != 64:
        raise ValueError("bad point length")
    x = int.from_bytes(data[:32], "big")
    y = int.from_bytes(data[32:], "big")
    if x == 0 and y == 0:
        return None
    if x >= P or y >= P or (y * y - x * x * x - 7) % P != 0:
        raise ValueError("point is not on the curve")
    return (x, y)


# ---------------------------------------------------------------- signatures

@dataclass(frozen=True)
class Signature:
    r_point: bytes
    s: int


def _challenge(r_point: bytes, pub: bytes, msg: bytes) -> int:
    return int.from_bytes(sha("schnorr", r_point, pub, msg), "big") % N


class Key:
    """A private key with its public key (64 raw bytes) cached."""

    def __init__(self, k: int):
        k %= N
        if k == 0:
            raise ValueError("zero key")
        self.k = k
        self.point = point_mul(k)
        self.pub = ser_point(self.point)

    @classmethod
    def generate(cls) -> "Key":
        return cls(secrets.randbelow(N - 1) + 1)

    def sign(self, msg: bytes) -> Signature:
        r = secrets.randbelow(N - 1) + 1
        r_point = ser_point(point_mul(r))
        e = _challenge(r_point, self.pub, msg)
        return Signature(r_point, (r + e * self.k) % N)


def verify(pub: bytes, msg: bytes, sig) -> bool:
    try:
        if not isinstance(sig, Signature):
            return False
        pub_point = deser_point(pub)
        r = deser_point(sig.r_point)
        if pub_point is None or r is None:
            return False
        e = _challenge(sig.r_point, pub, msg)
        return point_mul(sig.s) == point_add(r, point_mul(e, pub_point))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- Merkle trees

def _levels(leaves):
    level = [sha("leaf", leaf) for leaf in leaves]
    levels = [level]
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(sha("node", level[i], level[i + 1]))
            else:
                nxt.append(level[i])
        level = nxt
        levels.append(level)
    return levels


def merkle_root(leaves) -> bytes:
    if not leaves:
        return sha("empty")
    return _levels(leaves)[-1][0]


def merkle_proof(leaves, index: int):
    """Proof as a list of (sibling_hash, sibling_is_on_the_right)."""
    proof = []
    for level in _levels(leaves)[:-1]:
        sibling = index ^ 1
        if sibling < len(level):
            proof.append((level[sibling], sibling > index))
        index //= 2
    return proof


def merkle_verify(leaf: bytes, proof, root: bytes) -> bool:
    h = sha("leaf", leaf)
    for sibling, on_right in proof:
        h = sha("node", h, sibling) if on_right else sha("node", sibling, h)
    return h == root
