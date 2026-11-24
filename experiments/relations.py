"""Four ideas for shrinking the proof-checker encoding, each put to the same test.

Run with:  python experiments/relations.py            (needs py_ecc; about ten minutes)
           python experiments/relations.py c d        (only some of them)

The question each time: among a given set of stored numbers, is there ANY
equation of degree at most two that honest values always satisfy? The lock's
gates have degree two, so if there is none, no set of gates in those numbers
alone can tie them together, however cleverly it is written.

Method: draw many random honest instances, list every monomial of degree at
most two in the numbers, and compute the rank of that table modulo p. Each
missing rank is one relation. Controls check that known relations are found.

  Idea A: in a loop step that multiplies both pairings' lines in at once, drop
          one of the three stored values (the square, the lines' class, the product).
  Idea B: use values of four numbers, working up to factors from both subfields.
  Idea C: follow the moving point by its lines alone, without its x-coordinate.
  Idea D: values of four numbers again, this time inside a loop step: squaring,
          multiplying by one line, and both at once.

Results (seeded, so they repeat). A: no relation ties the result to what is
left once the square or the lines' class is dropped. B: no multiplication rule.
C: none between consecutive lines; with x stored at every other step, none
either. D: none. Each rules out one idea in the form tried, not every other.

Idea A's step is the one the "compact" and "rescaled" checks use. The
"bundled" check gets below it another way: it multiplies in one pairing's line
at a time (two forms of the running value make that take two stored values,
not three) and brings the other pairing's lines in a few steps at a time.
"""
import itertools, pathlib, random, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from py_ecc import bn128 as bn
from praxis_lock.encoding import pairing as pg
P = pg.P; FQ12 = bn.FQ12
rng = random.Random(2026)
ZERO, ONE, V = FQ12.zero(), FQ12.one(), pg._V
mul, inv = pg.fq12_mul, pg.fq6_inverse

def k_elem(a, b):                      # a + b u of F_p^2, inside F_p^12
    return FQ12([(a - 9 * b) % P, 0, 0, 0, 0, 0, b % P, 0, 0, 0, 0, 0])
def rand_k(): return k_elem(rng.randrange(P), rng.randrange(P))
def rand6(): return FQ12([rng.randrange(P) if i % 2 == 0 else 0 for i in range(12)])
def nums6(x): return pg.ints(x)[0::2]                         # six numbers of an F_p^6 element
def nums2(x): co = pg.ints(x); return [(co[0] + 9 * co[6]) % P, co[6]]   # two numbers of an F_p^2 element
tmul = lambda g1, g2: mul(mul(g1, g2) + V, inv(g1 + g2))

def nullity(rows, n_vars):
    """Number of independent relations of degree <= 2 among the columns' monomials."""
    mons = [()] + [(i,) for i in range(n_vars)] + list(itertools.combinations_with_replacement(range(n_vars), 2))
    table = []
    for r in rows:
        table.append([1 if not m else (r[m[0]] if len(m) == 1 else r[m[0]] * r[m[1]] % P) for m in mons])
    rank, col, n = 0, 0, len(mons)
    while rank < len(table) and col < n:
        piv = next((i for i in range(rank, len(table)) if table[i][col]), None)
        if piv is None:
            col += 1; continue
        table[rank], table[piv] = table[piv], table[rank]
        s = pow(table[rank][col], -1, P)
        row = [v * s % P for v in table[rank]]; table[rank] = row
        for i in range(len(table)):
            if i != rank and table[i][col]:
                f = table[i][col]; table[i] = [(a - f * b) % P for a, b in zip(table[i], row)]
        rank += 1; col += 1
    return n - rank, n

def draw_and_count(label, draw, wanted, expect=""):
    rows = []
    while len(rows) < wanted:
        row = draw()
        if row is not None:
            rows.append(row)
    k, n = nullity(rows, len(rows[0]))
    print(f"   {label:<66} {len(rows[0]):>2} numbers, {n:>3} monomials, relations found: {k}  {expect}", flush=True)
    return k


# ---------------------------------------------------------------- idea A: fewer stored values per step
# One loop step with fixed constants: g -> G = g*g -> g' = G * (class tau of the two lines).
m_c, t_c, l0 = rand_k(), rand_k(), rand_k()
rho, eta = rng.randrange(P), rng.randrange(P)
b_fixed = ZERO - (m_c * rho + mul(t_c * eta, V))
def one_step():
    g, mu, nu = rand6(), rand_k(), rand_k()
    a = mu + mul(nu, V)
    A, B = l0 + mul(V, mul(a, b_fixed)), mul(l0, b_fixed) + a
    tau = mul(A, inv(B)); G = tmul(g, g); g2 = tmul(G, tau)
    return dict(g=nums6(g), mu=nums2(mu), nu=nums2(nu), tau=nums6(tau), G=nums6(G), new=nums6(g2))

def idea_a():
    samples = [one_step() for _ in range(480)]
    def test(names, label, expect=""):
        rows = [sum((s[n] for n in names), []) for s in samples]
        k, n = nullity(rows, len(rows[0]))
        print(f"   {label:<62} {len(rows[0]):>2} numbers, {n:>3} monomials, relations found: {k}  {expect}", flush=True)
        return k
    print("IDEA A  one loop step with both pairings' lines: which stored values can be dropped?")
    test(["g", "G", "mu", "nu", "tau", "new"], "(control) everything the compact check stores")
    test(["g", "mu", "nu", "new"], "drop the square and the lines' class")
    tau_only = test(["tau"], "(control) the lines' class alone: it has only 4 free numbers")
    no_square = test(["g", "tau", "new"], "drop the square, keep the lines' class")
    print(f"      relations that involve the result: {no_square - tau_only}")
    no_new = test(["g", "G", "mu", "nu"], "(control) square and line values, without the result")
    with_new = test(["g", "G", "mu", "nu", "new"], "drop the lines' class, keep the square")
    print(f"      relations that involve the result: {with_new - no_new}")


# ---------------------------------------------------------------- idea B: values of four numbers
# Classes up to factors from both subfields. A class of six numbers g = g0 + g1 v + g2 v^2 is moved by
# g -> (g + k v^2) / (1 + k v g), k in F_p^2. A reduced value has g2 = 0: four numbers.
def k_poly_roots(coeffs):
    """Roots in F_p^2 of a cubic with coefficients in F_p^2 (as F_p^12 elements), lowest first."""
    lead = inv(coeffs[3]); f = [mul(cf, lead) for cf in coeffs[:3]]           # monic: x^3 + f2 x^2 + f1 x + f0
    def reduce(poly):
        poly = list(poly)
        while len(poly) > 3:
            top = poly.pop()
            for i in range(3):
                poly[len(poly) - 3 + i] = poly[len(poly) - 3 + i] - mul(top, f[i])
        return poly + [ZERO] * (3 - len(poly))
    def pmul(a, b):
        out = [ZERO] * 5
        for i, x in enumerate(a):
            for j, y in enumerate(b):
                out[i + j] = out[i + j] + mul(x, y)
        return reduce(out)
    def ppow(base, e):
        result = [ONE, ZERO, ZERO]
        for bit in bin(e)[2:]:
            result = pmul(result, result)
            if bit == "1": result = pmul(result, base)
        return result
    def gcd(a, b):
        a, b = [x for x in a], [x for x in b]
        def trim(p):
            while p and p[-1] == ZERO: p.pop()
            return p
        a, b = trim(a), trim(b)
        while b:
            while len(a) >= len(b) and a:
                s = mul(a[-1], inv(b[-1])); shift = len(a) - len(b)
                for i, y in enumerate(b): a[shift + i] = a[shift + i] - mul(s, y)
                a = trim(a)
            a, b = b, a
        return a
    xq = ppow([ZERO, ONE, ZERO], P * P)
    g = gcd(f + [ONE], [xq[0], xq[1] - ONE, xq[2]])                              # gcd(f, x^q - x)
    roots = []
    while len(g) > 2:                                                            # split by random shifts
        r = rand_k(); h = ppow([r, ONE, ZERO], (P * P - 1) // 2)
        d = gcd(list(g), [h[0] - ONE, h[1], h[2]])
        if 1 < len(d) < len(g): g = d
    if len(g) == 2: roots.append(ZERO - mul(g[0], inv(g[1])))
    return roots

def reduced(G):
    """A value with no v^2 part in the class of G, if the cubic for k has a root."""
    def F(k):
        N, D = G + mul(k, mul(V, V)), ONE + mul(k, mul(V, G))
        z = mul(N, inv(D)); co = pg.ints(z)
        return k_elem(co[4] + 9 * co[10], co[10]), z, D        # the v^2 part of z, as an F_p^2 number
    # The v^2 part of N * adj(D) is a cubic in k; rebuild it from four values.
    def numer(k):
        N, D = G + mul(k, mul(V, V)), ONE + mul(k, mul(V, G))
        d = pg.ints(D); D0, D1, D2 = (k_elem(d[2*i] + 9 * d[2*i+6], d[2*i+6]) for i in range(3))
        n_ = pg.ints(N); N0, N1, N2 = (k_elem(n_[2*i] + 9 * n_[2*i+6], n_[2*i+6]) for i in range(3))
        xi = k_elem(9, 1)
        adj0, adj1, adj2 = mul(D0, D0) - mul(xi, mul(D1, D2)), mul(xi, mul(D2, D2)) - mul(D0, D1), mul(D1, D1) - mul(D0, D2)
        return mul(N0, adj2) + mul(N1, adj1) + mul(N2, adj0)
    xs = [k_elem(i, 0) for i in range(4)]; ys = [numer(x) for x in xs]
    coeffs = [ZERO] * 4                                                          # Lagrange interpolation
    for i in range(4):
        basis, denom = [ONE], ONE
        for j in range(4):
            if j != i:
                basis = [ZERO - mul(xs[j], basis[0])] + [basis[t - 1] - mul(xs[j], basis[t]) if t < len(basis) else basis[t - 1] for t in range(1, len(basis) + 1)]
                denom = mul(denom, xs[i] - xs[j])
        s = mul(ys[i], inv(denom))
        for t in range(4): coeffs[t] = coeffs[t] + mul(basis[t], s)
    if coeffs[3] == ZERO: return None
    for k in k_poly_roots(coeffs):
        part, z, D = F(k)
        if part == ZERO: return z
    return None

def rand_reduced():
    return rand_k() + mul(rand_k(), V)
def nums4(z): co = pg.ints(z); return [co[0], co[2], co[6], co[8]]

def idea_b():
    print("\nIDEA B  values of four numbers: is there a degree-two rule for multiplying them?")
    t = time.time(); triples = []
    while len(triples) < 130:
        x, y = rand_reduced(), rand_reduced()
        z = reduced(tmul(x, y))
        if z is not None:
            co = pg.ints(z); assert co[4] == 0 and co[10] == 0
            triples.append(nums4(x) + nums4(y) + nums4(z))
    print(f"   {len(triples)} honest triples x * y = z drawn in {time.time() - t:.0f} s")
    k, n = nullity(triples, 12)
    print(f"   three reduced values, 12 numbers, {n} monomials, relations found: {k}")
    # control: the six-number classes do have their degree-two rule
    ctrl = []
    for _ in range(240):
        x, y = rand6(), rand6(); ctrl.append(nums6(x) + nums6(y) + nums6(tmul(x, y)))
    k, n = nullity(ctrl, 18)
    print(f"   (control) three six-number values, 18 numbers, {n} monomials, relations found: {k}")


# ---------------------------------------------------------------- idea C: the moving point by its lines alone
# The y-free point arithmetic stores, per doubling, the tangent's slope mu and intercept nu and
# the new x-coordinate: three numbers of F_p^2. The line needs mu and nu. Is x needed?
FQ2 = bn.FQ2
def rand_2(): return FQ2([rng.randrange(P), rng.randrange(P)])
def co2(v): return [int(v.coeffs[0]), int(v.coeffs[1])]

def tangent_chain(steps):
    """A random point on a random curve Y^2 = X^3 + b and the tangents at it and its doubles."""
    x, y = rand_2(), rand_2()
    b = y * y - x * x * x
    out = []
    for _ in range(steps):
        mu = 3 * x * x / (2 * y)
        nu = y - mu * x
        out.append(dict(x=x, mu=mu, nu=nu))
        nx = mu * mu - 2 * x
        x, y = nx, mu * (x - nx) - y
    out.append(dict(x=x))
    return b, out

def idea_c():
    print("\nIDEA C  the moving point followed by its lines alone, without x")
    def rows_of(pick, steps=3):
        def draw():
            b, chain = tangent_chain(steps)
            return sum((co2(v) for v in pick(b, chain)), [])
        return draw
    line = lambda c, i: [c[i]["mu"], c[i]["nu"]]
    control = draw_and_count("(control) two lines, the x between them and the next x",
                             rows_of(lambda b, c: line(c, 0) + [c[1]["x"]] + line(c, 1) + [c[2]["x"]]), 150,
                             "(six are used today)")
    one = draw_and_count("(control) one line and the curve's constant", rows_of(lambda b, c: [b] + line(c, 0)), 60)
    two = draw_and_count("two lines in a row and the curve's constant",
                         rows_of(lambda b, c: [b] + line(c, 0) + line(c, 1)), 110)
    print(f"      relations that tie the second line to the first: {two - 2 * one}")
    three = draw_and_count("three lines in a row and the curve's constant",
                           rows_of(lambda b, c: [b] + line(c, 0) + line(c, 1) + line(c, 2), 4), 180)
    print(f"      relations that tie the third line to the first two: {three - two - one}")
    before = draw_and_count("(control) x, the line there, and the next line",
                            rows_of(lambda b, c: [c[0]["x"]] + line(c, 0) + line(c, 1)), 110)
    after = draw_and_count("x stored at every other step: x, two lines, the x after both",
                           rows_of(lambda b, c: [c[0]["x"]] + line(c, 0) + line(c, 1) + [c[2]["x"]]), 150)
    print(f"      relations that involve the later x: {after - before}   (six would be needed)")


# ---------------------------------------------------------------- idea D: four-number values in a loop step
# Idea B asked for a rule to multiply two reduced values. A loop step needs less: the square of
# one reduced value, and its product with a line, which has a special shape.
def idea_d():
    print("\nIDEA D  values of four numbers inside one loop step")
    def line_class():
        mu, nu = rand_k(), rand_k()
        return mu, nu, mul(l0, inv(mu + mul(nu, V)))           # the class of the line l0 + (mu + nu v) w
    def squared():
        x = rand_reduced(); z = reduced(tmul(x, x))
        return None if z is None else nums4(x) + nums4(z)
    def times_line():
        x = rand_reduced(); mu, nu, cls = line_class(); z = reduced(tmul(x, cls))
        return None if z is None else nums4(x) + nums2(mu) + nums2(nu) + nums4(z)
    def whole_step():
        x = rand_reduced(); mu, nu, cls = line_class(); z = reduced(tmul(tmul(x, x), cls))
        return None if z is None else nums4(x) + nums2(mu) + nums2(nu) + nums4(z)
    def line_alone():
        x = rand_reduced(); mu, nu, cls = line_class()
        return nums4(x) + nums2(mu) + nums2(nu)
    t = time.time()
    draw_and_count("a reduced value and its reduced square", squared, 70)
    base = draw_and_count("(control) a reduced value and a line, no result", line_alone, 70)
    k = draw_and_count("a reduced value, a line, and their reduced product", times_line, 130)
    print(f"      relations that involve the result: {k - base}")
    k = draw_and_count("a reduced value, a line, and the reduced (square times line)", whole_step, 130)
    print(f"      relations that involve the result: {k - base}")
    print(f"   ({time.time() - t:.0f} s)")


if __name__ == "__main__":
    chosen = [a.lower() for a in sys.argv[1:]] or ["a", "b", "c", "d"]
    for name, run in (("a", idea_a), ("b", idea_b), ("c", idea_c), ("d", idea_d)):
        if name in chosen:
            run()
