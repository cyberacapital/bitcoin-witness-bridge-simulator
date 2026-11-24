"""Draws the four pictures used in the README.

Run with:  python docs/figures.py        (no dependencies; writes the .svg files next to itself)

The pictures are plain SVG with their own light background, so they read the
same on a light and a dark page. Texts are set line by line; if you change one,
open the file in a browser and check that it still fits its box.
"""
import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent

INK, MUTED, LINE, PAPER, EDGE = "#1F2328", "#57606A", "#D0D7DE", "#FCFBF8", "#E5E1D8"
BTC, BTC_SOFT = "#C96A00", "#FFF1DD"            # things that happen on Bitcoin
SOL, SOL_SOFT = "#6D3FD6", "#F1EAFE"            # things that happen on Solana
FONT = "Helvetica, Arial, 'Liberation Sans', sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"

# How far along a part is in this repository: (text colour, background).
STATUS = {
    "MODELLED HERE": ("#1A7F37", "#DDF4E4"),
    "TOY DEMO HERE": ("#0969DA", "#DDF0FF"),
    "TOY SIZE": ("#0969DA", "#DDF0FF"),
    "BUILT, WITH GAPS": ("#9A6700", "#FFF4D4"),
    "ONE RULE WRITTEN": ("#9A6700", "#FFF4D4"),
    "NOT CODED": ("#57606A", "#EAEEF2"),
}


class Picture:
    def __init__(self, width, height, title, description):
        self.w, self.h, self.parts = width, height, []
        self.add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
                 f'height="{height}" role="img" aria-labelledby="t d" font-family="{FONT}">')
        self.add(f'<title id="t">{escape(title)}</title>')
        self.add(f'<desc id="d">{escape(description)}</desc>')
        self.add('<defs><marker id="a" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" '
                 f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{MUTED}"/></marker></defs>')
        self.add(f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="14" fill="{PAPER}" stroke="{EDGE}"/>')

    def add(self, markup):
        self.parts.append(markup)

    def text(self, x, y, words, size=12.5, fill=INK, weight=None, anchor=None, style=None, family=None, spacing=None):
        extra = "".join(f' {name}="{value}"' for name, value in (
            ("font-weight", weight), ("text-anchor", anchor), ("font-style", style),
            ("font-family", family), ("letter-spacing", spacing)) if value)
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}"{extra}>{escape(words)}</text>')

    def lines(self, x, y, rows, leading=17.5, **look):
        for i, row in enumerate(rows):
            self.text(x, y + leading * i, row, **look)

    def heading(self, title, subtitle):
        self.text(40, 52, title, size=24, weight=700)
        self.text(40, 78, subtitle, size=14, fill=MUTED)

    def box(self, x, y, w, h, fill="#FFFFFF", stroke=LINE, dashed=False):
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}"{dash}/>')

    def tag(self, x, y, words, colour, soft, anchor="start"):
        """A small rounded label. (x, y) is its left end and text baseline, or its right end if anchor is 'end'."""
        width = round(8.2 * len(words) + 16)
        left = x - width if anchor == "end" else x
        self.add(f'<rect x="{left}" y="{y - 13}" width="{width}" height="19" rx="9.5" fill="{soft}"/>')
        self.text(left + 8, y + 1, words, size=11, weight=700, fill=colour, spacing="0.8")
        return width

    def status(self, x, y, words, anchor="start"):
        return self.tag(x, y, words, *STATUS[words], anchor=anchor)

    def badge(self, x, y, words, colour, soft):
        """A number in a pill; returns its width."""
        width = 22 if len(words) == 1 else 36
        self.add(f'<rect x="{x}" y="{y}" width="{width}" height="22" rx="11" fill="{soft}"/>')
        self.text(x + width / 2, y + 15.5, words, weight=700, anchor="middle", fill=colour)
        return width

    def arrow(self, x1, y1, x2, y2):
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{MUTED}" stroke-width="1.6" marker-end="url(#a)"/>')

    def padlock(self, cx, cy, hole="#F6F8FA"):
        self.add(f'<path d="M{cx - 13} {cy - 6} v-9 a13 13 0 0 1 26 0 v9" fill="none" stroke="{INK}" '
                 f'stroke-width="5" stroke-linecap="round"/>')
        self.add(f'<rect x="{cx - 21}" y="{cy - 6}" width="42" height="32" rx="6" fill="{INK}"/>')
        self.add(f'<circle cx="{cx}" cy="{cy + 7}" r="4.2" fill="{hole}"/>'
                 f'<rect x="{cx - 1.6}" y="{cy + 8}" width="3.2" height="9" rx="1.6" fill="{hole}"/>')

    def footnote(self, y, bold, rest, dot="#B35900"):
        self.add(f'<circle cx="46" cy="{y - 4.5}" r="4.5" fill="{dot}"/>')
        self.add(f'<text x="58" y="{y}" font-size="12.5" fill="{INK}"><tspan font-weight="700">{escape(bold)}</tspan>'
                 f' {escape(rest)}</text>')

    def save(self, name):
        (HERE / name).write_text("\n".join(self.parts + ["</svg>"]) + "\n")


def step(p, x, y, number, title, rows, colour, soft, w=200, h=120):
    p.box(x, y, w, h)
    p.add(f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="2.5" fill="{colour}"/>')
    width = p.badge(x + 16, y + 13, number, colour, soft)
    p.text(x + 16 + width + 9, y + 29.5, title, size=15.5, weight=700)
    p.lines(x + 16, y + 58, rows)


# ---------------------------------------------------------------- 1. the bridge, at the top of the README

def overview():
    p = Picture(960, 640, "How the simulated bridge works, and what is in this repository",
                "Bitcoin is sent to a vault whose key is sealed in a lock. Solana mints the same amount as tokens. "
                "Tokens are used to launch and trade coins. Burning tokens gives a proof that opens the lock and "
                "releases the Bitcoin. The repository holds a model of the bridge, the lock scheme at toy size, "
                "and a proof checker written for the lock.")
    p.heading("A Bitcoin to Solana bridge with no operator",
              "Bitcoin is locked so that only proof of a burn on Solana can release it. "
              "This repository models that from end to end.")
    cols, top, low, w, h = [40, 267, 493, 720], 124, 312, 200, 120

    p.tag(40, 110, "ON BITCOIN", BTC, BTC_SOFT)
    step(p, cols[0], top, "1", "Seal", ["A vault is made. Its key is", "cut into pieces, each sealed", "in a lock, then deleted."], BTC, BTC_SOFT)
    step(p, cols[1], top, "2", "Deposit", ["A user sends Bitcoin to", "the vault."], BTC, BTC_SOFT)
    step(p, cols[3], top, "7", "Open", ["The proof opens the lock.", "The burner gets the key and", "moves the Bitcoin."], BTC, BTC_SOFT)
    p.box(cols[2], top, w, h, fill="#F6F8FA", stroke=MUTED, dashed=True)
    p.padlock(cols[2] + w / 2, top + 44)
    p.text(cols[2] + w / 2, top + 92, "Nobody holds the key.", weight=700, anchor="middle")
    p.text(cols[2] + w / 2, top + 109, "Opens only for a valid proof.", fill=MUTED, anchor="middle")

    p.tag(40, low + 16, "ON SOLANA", SOL, SOL_SOFT)
    p.text(40, low + 50, "The rule that always holds:", fill=MUTED)
    p.lines(40, low + 72, ["tokens in circulation", "= Bitcoin sealed in vaults"], leading=19, size=14, weight=700)
    step(p, cols[1], low, "3", "Mint", ["Solana checks the deposit", "against Bitcoin's blocks and", "mints the same amount."], SOL, SOL_SOFT)
    step(p, cols[2], low, "4–5", "Launch, trade", ["Tokens move like any others:", "launch a coin against them,", "buy and sell it."], SOL, SOL_SOFT)
    step(p, cols[3], low, "6", "Burn", ["A holder burns tokens and", "attaches the fingerprint of", "a secret."], SOL, SOL_SOFT)

    for i in range(3):
        p.arrow(cols[i] + w + 3, top + h / 2, cols[i + 1] - 4, top + h / 2)
    for i in (1, 2):
        p.arrow(cols[i] + w + 3, low + h / 2, cols[i + 1] - 4, low + h / 2)
    p.arrow(cols[1] + w / 2, top + h + 4, cols[1] + w / 2, low - 5)
    p.arrow(cols[3] + w / 2, low - 4, cols[3] + w / 2, top + h + 5)
    between = (top + h + low) / 2 + 4.5
    p.text(cols[1] + w / 2 + 12, between, "proof of deposit", fill=MUTED, style="italic")
    p.text(cols[3] + w / 2 - 12, between, "proof of burn, plus the secret", fill=MUTED, style="italic", anchor="end")

    p.add(f'<line x1="40" y1="462" x2="920" y2="462" stroke="{EDGE}"/>')
    p.text(40, 490, "What is in the repository", size=15.5, weight=700)
    cards = [("bridge_sim/", ["The whole bridge as a runnable model,", "attacks included. Its lock is simulated."]),
             ("praxis_lock/", ["The lock scheme itself, from the Praxis", "paper, at toy size with real pairings."]),
             ("praxis_lock/encoding/", ["A proof checker rewritten for the lock.", "Runs on real proofs, 1.4× the paper's size."])]
    for i, (name, rows) in enumerate(cards):
        x = 40 + 300 * i
        p.box(x, 504, 280, 84)
        p.text(x + 16, 531, name, size=13.5, weight=700, family=MONO)
        p.lines(x + 16, 553, rows, leading=17, fill=MUTED)
    p.footnote(618, "Simulator only.", "No real network, no real money, not audited.")
    p.save("overview.svg")


# ---------------------------------------------------------------- 2. one lock, many uses

def uses():
    p = Picture(960, 668, "One lock, many uses, and which of them are coded in this repository",
                "A secret is sealed to a rule and opens for whoever can prove the rule was met. Six uses: a bridge "
                "without a custodian (modelled here), a reward for a solution (toy demo here), and four that are "
                "not coded: a time lock, sending to a credential, paying on Bitcoin when a proof exists, and "
                "spending rules on Bitcoin.")
    p.heading("One lock, many uses",
              "The lock opens for whoever can prove that a rule was met. Change the rule and the same lock does another job.")

    # the pattern
    y, h = 104, 92
    p.box(40, y, 250, h)
    p.text(60, y + 30, "A secret", size=15.5, weight=700)
    p.lines(60, y + 53, ["a key, a file, a message"], fill=MUTED)
    p.box(355, y, 250, h, fill="#F6F8FA", stroke=MUTED, dashed=True)
    p.padlock(397, y + 43)
    p.text(432, y + 30, "sealed to a rule", size=15.5, weight=700)
    p.lines(432, y + 53, ["“someone can prove", "that ...”"], fill=MUTED, style="italic")
    p.box(670, y, 250, h)
    p.text(690, y + 30, "opens for any prover", size=15.5, weight=700)
    p.lines(690, y + 53, ["no operator and no judge,", "even if the sealer is gone"], fill=MUTED)
    p.arrow(293, y + h / 2, 351, y + h / 2)
    p.arrow(608, y + h / 2, 666, y + h / 2)

    p.text(40, 236, "The same lock, with six different rules", size=15.5, weight=700)
    cards = [
        ("MODELLED HERE", "Bridge without a custodian",
         ["“these tokens were burned on Solana,", "and I know the secret behind the burn”"],
         ["bridge_sim/, with a simulated lock"]),
        ("TOY DEMO HERE", "Reward for a solution",
         ["“here is a solution to this puzzle”", "(in the demo: two factors of 47,053)"],
         ["demo_lock.py, with the real lock at toy size"]),
        ("NOT CODED", "Time lock",
         ["“Bitcoin has grown 52,560 blocks", "past block X” (about a year)"],
         ["the block checks exist in bridge_sim/"]),
        ("NOT CODED", "Sending to a credential",
         ["“I hold a certificate from this registry", "that says: licensed doctor”"],
         ["needs a rule that checks a signature"]),
        ("NOT CODED", "Paying on Bitcoin for a proof",
         ["“this program ran on this input", "and gave this result”"],
         ["the proof checker is the building block"]),
        ("NOT CODED", "Spending rules on Bitcoin",
         ["“this spend follows the rule", "the coins were sealed under”"],
         ["called covenants; needs extra design"]),
    ]
    for i, (state, title, rule, where) in enumerate(cards):
        x, y = 40 + 300 * (i % 3), 252 + 176 * (i // 3)
        p.box(x, y, 280, 160)
        p.status(x + 16, y + 28, state)
        p.text(x + 16, y + 58, title, size=15.5, weight=700)
        p.text(x + 16, y + 80, "Opens for whoever can prove:", size=11.5, fill=MUTED)
        p.lines(x + 16, y + 99, rule, leading=17, style="italic")
        p.add(f'<line x1="{x + 16}" y1="{y + 126}" x2="{x + 264}" y2="{y + 126}" stroke="{EDGE}"/>')
        p.lines(x + 16, y + 145, where, size=11.5, fill=MUTED)
    p.footnote(644, "Only the rule changes.", "The lock and the proof checker are the same for every use. "
               "None of it runs at real size here.")
    p.save("uses.svg")


# ---------------------------------------------------------------- 3. from a rule to a lock

def pipeline():
    p = Picture(960, 644, "From a rule to a lock: the five stages and what this repository has of each",
                "Stage 1, the rule as a program: only the bridge rule is written, in plain Python. Stage 2, a proof "
                "that the program said yes: not coded. Stage 3, one small Groth16 proof: a toy system that makes "
                "real test proofs. Stage 4, the proof checker as lock gates: built, with gaps, 1.4 times the size "
                "the paper reports. Stage 5, the lock: built at toy size.")
    p.heading("From a rule to a lock: what is built here",
              "Every use goes through the same five stages. Only the first is written for each use.")
    p.text(96, 116, "STAGE", size=11, weight=700, fill=MUTED, spacing="0.8")
    p.text(500, 116, "IN THIS REPOSITORY", size=11, weight=700, fill=MUTED, spacing="0.8")
    stages = [
        ("The rule", ["A program that looks at evidence", "and answers yes or no."],
         "ONE RULE WRITTEN", ["The bridge's rule, in plain Python", "(bridge_sim/lock.py). No other rule yet."]),
        ("Proof of the run", ["A zero-knowledge machine proves that", "the program said yes."],
         "NOT CODED", ["The paper uses an existing machine.", "A rule would be rewritten for it."]),
        ("One small proof", ["That proof is wrapped into a single", "Groth16 proof of fixed size."],
         "TOY SIZE", ["A small Groth16 system that makes real", "test proofs (encoding/groth16.py)."]),
        ("Proof checker as lock gates", ["“This proof is valid”, rewritten as", "equations over bits."],
         "BUILT, WITH GAPS", ["Checks real proofs, at 1.4× the size the", "paper reports (praxis_lock/encoding/)."]),
        ("The lock", ["A key sealed so that only a solution", "of those equations opens it."],
         "TOY SIZE", ["The real scheme, on small statements.", "Full size, about 8 GB a key piece, does not run."]),
    ]
    top, h, gap = 128, 80, 18
    for i, (title, what, state, here) in enumerate(stages):
        y = top + i * (h + gap)
        p.box(40, y, 880, h)
        p.badge(56, y + 29, str(i + 1), INK, "#EAEEF2")
        p.text(96, y + 27, title, size=15.5, weight=700)
        p.lines(96, y + 48, what, leading=17, fill=MUTED)
        p.add(f'<line x1="484" y1="{y + 12}" x2="484" y2="{y + h - 12}" stroke="{EDGE}"/>')
        p.status(500, y + 26, state)
        p.lines(500, y + 48, here, leading=17)
        if i:
            p.arrow(67, y - gap + 2, 67, y - 3)
    p.footnote(top + 5 * (h + gap) + 14, "Stages 2 to 5 are the same for every use.",
               "A new use needs only a new rule in stage 1.", dot=INK)
    p.save("pipeline.svg")


# ---------------------------------------------------------------- 4. three other locks, step by step

def examples():
    p = Picture(960, 572, "Three other locks, step by step: a time lock, a reward for a solution, sending to a credential",
                "Each follows the bridge's pattern: seal a secret to a rule, wait until the rule can be proven, open. "
                "Time lock: sealed to Bitcoin growing by a number of blocks; anyone opens it by showing the block "
                "headers. Reward: sealed to a solution of a puzzle; the solver opens it. Credential: sealed to "
                "holding a certificate; any holder opens it.")
    p.heading("Three other locks, step by step",
              "Each follows the bridge's pattern: seal a secret to a rule, wait until the rule can be proven, open.")
    rows = [
        ("Time lock", "NOT CODED",
         ["Anyone seals a message to: “Bitcoin", "has grown 52,560 blocks past block X.”"],
         ["About a year of blocks is mined.", "Nobody has to do anything."],
         ["Anyone shows the block headers", "and reads the message."]),
        ("Reward for a solution", "TOY DEMO HERE",
         ["A sponsor seals the key to a reward", "to: “here is a solution to this puzzle.”"],
         ["Someone finds a solution.", "The sponsor can be long gone."],
         ["The solver opens the lock and takes", "the reward. No judge decides."]),
        ("Sending to a credential", "NOT CODED",
         ["A sender seals a file to: “I hold this", "registry's certificate for a doctor.”"],
         ["A doctor holds such a certificate,", "or is issued one later."],
         ["Any holder opens it. The sender", "never learns who did."]),
    ]
    top, h, pitch = 110, 88, 138
    for i, (title, state, seal, then, opened) in enumerate(rows):
        y = top + i * pitch
        p.text(40, y + 14, title, size=15.5, weight=700)
        p.status(40 + round(8.7 * len(title)) + 14, y + 13, state)
        for j, (x, label, body) in enumerate(((40, "SEAL", seal), (340, "THEN", then), (640, "OPEN", opened))):
            p.box(x, y + 28, 280, h)
            p.text(x + 16, y + 50, label, size=11, weight=700, fill=MUTED, spacing="0.8")
            p.lines(x + 16, y + 73, body, leading=18)
            if j:
                p.arrow(x - 17, y + 28 + h / 2, x - 4, y + 28 + h / 2)
    p.footnote(top + 3 * pitch + 22, "Limits shared by all of them:",
               "the rule is fixed when the lock is made, and whoever holds a proof can open.")
    p.save("examples.svg")


if __name__ == "__main__":
    for draw in (overview, uses, pipeline, examples):
        draw()
    print("wrote", ", ".join(sorted(f.name for f in HERE.glob("*.svg"))))
