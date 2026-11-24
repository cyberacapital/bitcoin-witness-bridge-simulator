"""Draws the nine figures used in the README.

Run with:  python docs/figures.py        (no dependencies; writes the .svg files next to itself)

One style throughout: solid red bands with white words for headings, light grey
cards with a coloured bar down the left edge, and white tiles with a red bar on
top. Red marks the design, grey marks today's way, risks and things not coded,
green marks the result. The figures are plain SVG on a white ground, so they read
the same on a light and a dark page. They carry no figure numbers; the README
gives each one its number, caption and source. Texts are set line by line; if
you change one, open the file in a browser and check that it still fits its card.
"""
import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent

RED, DARK, GREY, GREEN = "#AF292E", "#8A2024", "#505A5F", "#00703C"
INK, SHADE, LINE, PAPER = "#0B0C0C", "#F3F2F1", "#B1B4B6", "#FFFFFF"
FONT = "Arial, Helvetica, 'Liberation Sans', sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
LEFT, RIGHT, FULL = 40, 490, 880        # two columns of 430, or the full width
THIRDS = [40, 340, 640]                 # three columns of 280

# How far along a part is in this repository: (words colour, fill, border, bar of its card).
STATUS = {
    "MODELLED HERE": (PAPER, RED, RED, RED),
    "TOY DEMO HERE": (RED, PAPER, RED, RED),
    "TOY SIZE": (RED, PAPER, RED, RED),
    "TWO RULES WRITTEN": (RED, PAPER, RED, RED),
    "BUILT, WITH GAPS": (RED, PAPER, RED, RED),
    "NOT CODED": (PAPER, GREY, GREY, GREY),
}


class Picture:
    def __init__(self, height, title, description, width=960):
        self.w, self.h, self.parts = width, height, []
        self.add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
                 f'height="{height}" role="img" aria-labelledby="t d" font-family="{FONT}">')
        self.add(f'<title id="t">{escape(title)}</title>')
        self.add(f'<desc id="d">{escape(description)}</desc>')
        heads = "".join(
            f'<marker id="{name}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
            f'orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="{colour}" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round"/></marker>' for name, colour in (("r", RED), ("g", GREY)))
        self.add(f"<defs>{heads}</defs>")
        self.add(f'<rect width="{width}" height="{height}" fill="{PAPER}"/>')

    def add(self, markup):
        self.parts.append(markup)

    def text(self, x, y, words, size=12.5, fill=GREY, weight=None, anchor=None, style=None, family=None, spacing=None):
        extra = "".join(f' {name}="{value}"' for name, value in (
            ("font-weight", weight), ("text-anchor", anchor), ("font-style", style),
            ("font-family", family), ("letter-spacing", spacing)) if value)
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}"{extra}>{escape(words)}</text>')

    def lines(self, x, y, rows, leading=17, **look):
        for i, row in enumerate(rows):
            self.text(x, y + leading * i, row, **look)

    def band(self, x, y, w, words, fill=RED, h=40, centred=False, aside=None):
        """A solid band with white words; `aside` is a short note in capitals at its right end."""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"/>')
        self.text(x + w / 2 if centred else x + 20, y + h / 2 + 5.5, words, size=15.5, fill=PAPER, weight=700,
                  anchor="middle" if centred else None)
        if aside:
            self.text(x + w - 20, y + h / 2 + 4, aside, size=11, fill=PAPER, weight=700, anchor="end", spacing="0.8")

    def card(self, x, y, w, h, title, rows=(), bar=RED, number=None, mono=False, look=None):
        """A grey card with a coloured bar on its left: a bold title, then lines of smaller words."""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{SHADE}"/>')
        self.add(f'<rect x="{x}" y="{y}" width="6" height="{h}" fill="{bar}"/>')
        left = x + 22
        if number:
            width = 22 if len(number) == 1 else 36
            self.add(f'<rect x="{left}" y="{y + 11}" width="{width}" height="22" fill="{bar}"/>')
            self.text(left + width / 2, y + 26.5, number, fill=PAPER, weight=700, anchor="middle")
            left += width + 9
        self.text(left, y + 27, title, size=15, fill=INK, weight=700, family=MONO if mono else None)
        self.lines(x + 22, y + 47, rows, **(look or {}))

    def tile(self, x, y, w, h, title, rows=(), mono=False):
        """A white tile with a red bar on top."""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{PAPER}" stroke="{LINE}" stroke-width="0.75"/>')
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="4" fill="{RED}"/>')
        self.text(x + 18, y + 31, title, size=13.5 if mono else 15, fill=INK, weight=700, family=MONO if mono else None)
        self.lines(x + 18, y + 51, rows)

    def tag(self, x, y, words):
        """A small status label; (x, y) is its left end and text baseline."""
        colour, fill, border, _ = STATUS[words]
        width = round(8.2 * len(words) + 16)
        self.add(f'<rect x="{x}" y="{y - 13}" width="{width}" height="19" fill="{fill}" stroke="{border}"/>')
        self.text(x + 8, y + 1, words, size=11, fill=colour, weight=700, spacing="0.8")

    def arrow(self, x1, y1, x2, y2, colour=RED):
        head = "r" if colour == RED else "g"
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{colour}" stroke-width="1.6" '
                 f'marker-end="url(#{head})"/>')

    def branch(self, y, top=64):
        """Lines from the heading band down to the two column bands that start at height y."""
        mid = (top + y) / 2 - 2
        for x in (LEFT + 215, RIGHT + 215):
            self.add(f'<path d="M480 {top} L480 {mid} L{x} {mid} L{x} {y - 4}" fill="none" stroke="{RED}" '
                     f'stroke-width="1.6" marker-end="url(#r)"/>')

    def strip(self, y, title, words):
        """The closing note: one wide card with a grey bar. The picture should end 20 below it."""
        self.card(LEFT, y, FULL, 60, title, [words], bar=GREY)

    def save(self, name):
        (HERE / name).write_text("\n".join(self.parts + ["</svg>"]) + "\n")


def two_columns(p, y, left, right, h=60, pitch=70, tags=None):
    """Two columns of title-and-line cards, as in the use-case figures."""
    for x, column in ((LEFT, left), (RIGHT, right)):
        for i, (title, words) in enumerate(column):
            p.card(x, y + pitch * i, 430, h, title, [words])
            if tags and title in tags:
                state = tags[title]
                p.tag(x + 430 - 14 - round(8.2 * len(state) + 16), y + pitch * i + 34, state)


# ---------------------------------------------------------------- 1. what the paper solves

def solves():
    p = Picture(476, "What the paper solves",
                "Left column, today: the two ways to use Bitcoin as a guarantee both depend on other people, a "
                "company or a set of watchers. Right column, with the paper: coins locked on Bitcoin open for "
                "whoever shows a valid proof. Bottom row, what the paper changed: about 8 GB per lock, seconds to "
                "lock or unlock as claimed, and no change to Bitcoin.")
    p.band(LEFT, 20, 430, "Today: trust other people", fill=GREY)
    p.band(RIGHT, 20, 430, "With the paper: trust a proof")
    today = [("Option 1: trust a company", "Risk: it fails or freezes your coins"),
             ("Option 2: trust watchers", "Risk: one dispute cost over $14,000"),
             ("Either way", "You depend on other people")]
    paper = [("Coins are locked on Bitcoin", "No person holds the key", RED),
             ("Someone shows a valid proof", "Example: the loan was repaid", RED),
             ("The lock opens by itself", "No keeper, no dispute, no new rules", GREEN)]
    for i, (title, words) in enumerate(today):
        p.card(LEFT, 72 + 92 * i, 430, 60, title, [words], bar=GREY)
    for i, (title, words, bar) in enumerate(paper):
        p.card(RIGHT, 72 + 92 * i, 430, 60, title, [words], bar=bar)
        if i:
            p.arrow(RIGHT + 215, 72 + 92 * i - 28, RIGHT + 215, 72 + 92 * i - 5)
    p.band(LEFT, 346, FULL, "What the paper changed", fill=DARK, h=36)
    changed = [("About 8 GB per lock", "Down from 100s of TB"), ("Seconds (claimed)", "To lock or unlock"),
               ("No Bitcoin changes", "Needs no soft fork")]
    for x, (title, words) in zip(THIRDS, changed):
        p.tile(x, 392, 280, 64, title, [words])
    p.save("solves.svg")


# ---------------------------------------------------------------- 2. use cases, and the far-future ones

def use_cases():
    p = Picture(516, "Use cases",
                "Two groups. Money on Bitcoin: borrowing against Bitcoin, moving Bitcoin to other networks (modelled "
                "here), self-custody safes, prizes that pay themselves (toy demo here). Alarms and information: "
                "tripwire alarms, time capsules (modelled here), an inheritance switch, credential-locked files. "
                "The paper is new and unreviewed and needs an honest one-time setup.")
    p.band(200, 20, 560, "Use cases", h=44, centred=True)
    p.branch(100)
    p.band(LEFT, 100, 430, "Money on Bitcoin", fill=DARK, h=38)
    p.band(RIGHT, 100, 430, "Alarms and information", fill=DARK, h=38)
    two_columns(p, 148,
                [("Borrow against Bitcoin", "No company holds your coins"),
                 ("Move Bitcoin to other networks", "Without a trusted keeper"),
                 ("Self-custody safes", "Delays and approved addresses"),
                 ("Prizes that pay themselves", "Money released by a correct proof")],
                [("Tripwire alarms", "Coins move if encryption is broken"),
                 ("Time capsules", "Opens when Bitcoin reaches a block"),
                 ("Inheritance switch", "Releases keys after missed check-ins"),
                 ("Credential-locked files", "Readable only with proof of ID")],
                tags={"Move Bitcoin to other networks": "MODELLED HERE", "Time capsules": "MODELLED HERE",
                      "Prizes that pay themselves": "TOY DEMO HERE"})
    p.strip(436, "Not yet proven", "New and unreviewed; needs an honest one-time setup")
    p.save("use-cases.svg")


def far_future():
    p = Picture(446, "Far-future use cases",
                "Speculation, in two groups. Space and deep time: trade across light-minutes, machines that own "
                "themselves, knowledge with an entrance exam. AI and other minds: answers from any kind of mind, a "
                "superintelligence seismograph, human and AI treaties. Three things are needed first: the paper "
                "survives review, sensors can be trusted, and a quantum-safe version exists.")
    p.band(200, 20, 560, "Far-future use cases", h=44, centred=True)
    p.branch(100)
    p.band(LEFT, 100, 430, "Space and deep time", fill=DARK, h=38)
    p.band(RIGHT, 100, 430, "AI and other minds", fill=DARK, h=38)
    two_columns(p, 148,
                [("Trade across light-minutes", "Pays on proof, no contact needed"),
                 ("Machines that own themselves", "Pays whoever proves a repair"),
                 ("Knowledge with an entrance exam", "Opens for whoever proves theorems")],
                [("Answers from any kind of mind", "Check the answer, not the mind"),
                 ("Superintelligence seismograph", "Harder locks open as AI advances"),
                 ("Human and AI treaties", "Stake lost on a broken promise")])
    p.strip(366, "Needs three things first", "Paper survives review, trusted sensors, quantum-safe version")
    p.save("far-future.svg")


# ---------------------------------------------------------------- 3. the bridge, at the top of the README

def overview():
    p = Picture(656, "A Bitcoin to Solana bridge with no operator",
                "Bitcoin is sent to a vault whose key is sealed in a lock. Solana mints the same amount as tokens. "
                "Tokens are used to launch and trade coins. Burning tokens gives a proof that opens the lock and "
                "releases the Bitcoin. The repository holds a model of the bridge, the lock scheme at toy size, "
                "and a proof checker written for the lock.")
    p.band(LEFT, 20, FULL, "A Bitcoin to Solana bridge with no operator", h=44)
    cols, top, low, w, h = [40, 267, 493, 720], 118, 300, 200, 112

    p.band(LEFT, 78, 200, "On Bitcoin", fill=DARK, h=30)
    p.card(cols[0], top, w, h, "Seal", ["A vault is made. Its key is", "cut into pieces, each sealed", "in a lock, then deleted."], number="1")
    p.card(cols[1], top, w, h, "Deposit", ["A user sends Bitcoin to", "the vault."], number="2")
    p.card(cols[2], top, w, h, "Nobody holds the key", ["The Bitcoin waits in the", "vault. The lock opens only", "for a valid proof."], bar=GREY)
    p.card(cols[3], top, w, h, "Open", ["The proof opens the lock.", "The burner gets the key and", "moves the Bitcoin."], bar=GREEN, number="7")

    p.band(LEFT, low, 200, "On Solana", fill=DARK, h=30)
    p.text(LEFT, low + 58, "The rule that always holds:")
    p.lines(LEFT, low + 80, ["tokens in circulation", "= Bitcoin sealed in vaults"], leading=19, size=14, fill=INK, weight=700)
    p.card(cols[1], low, w, h, "Mint", ["Solana checks the deposit", "against Bitcoin's blocks and", "mints the same amount."], number="3")
    p.card(cols[2], low, w, h, "Launch, trade", ["Tokens move like any others:", "launch a coin against them,", "buy and sell it."], number="4–5")
    p.card(cols[3], low, w, h, "Burn", ["A holder burns tokens and", "attaches the fingerprint of", "a secret."], number="6")

    for i in range(3):
        p.arrow(cols[i] + w + 4, top + h / 2, cols[i + 1] - 5, top + h / 2)
    for i in (1, 2):
        p.arrow(cols[i] + w + 4, low + h / 2, cols[i + 1] - 5, low + h / 2)
    p.arrow(cols[1] + w / 2, top + h + 5, cols[1] + w / 2, low - 6)
    p.arrow(cols[3] + w / 2, low - 5, cols[3] + w / 2, top + h + 6)
    between = (top + h + low) / 2 + 4.5
    p.text(cols[1] + w / 2 + 12, between, "proof of deposit", style="italic")
    p.text(cols[3] + w / 2 - 12, between, "proof of burn, plus the secret", style="italic", anchor="end")

    p.band(LEFT, 436, FULL, "What is in the repository", fill=DARK, h=36)
    tiles = [("bridge_sim/", ["The whole bridge as a runnable model,", "attacks included. Its lock is simulated."]),
             ("praxis_lock/", ["The lock scheme itself, from the Praxis", "paper, at toy size with real pairings."]),
             ("praxis_lock/encoding/", ["A proof checker rewritten for the lock.", "Runs on real proofs, 1.4× the paper's size."])]
    for x, (name, rows) in zip(THIRDS, tiles):
        p.tile(x, 482, 280, 84, name, rows, mono=True)
    p.strip(576, "Simulator only", "No real network, no real money, not audited")
    p.save("overview.svg")


# ---------------------------------------------------------------- 4. one lock, many uses: which are coded

def uses():
    p = Picture(634, "One lock, many uses",
                "A secret is sealed to a rule and opens for whoever can prove the rule was met. Six uses: a bridge "
                "without a custodian and a time lock (both modelled here), a reward for a solution (toy demo "
                "here), and three that are not coded: sending to a credential, paying on Bitcoin when a proof "
                "exists, and spending rules on Bitcoin.")
    p.band(200, 20, 560, "One lock, many uses", h=44, centred=True)
    y, h = 84, 80
    p.card(THIRDS[0], y, 280, h, "A secret", ["a key, a file, a message"])
    p.card(THIRDS[1], y, 280, h, "sealed to a rule", ["“someone can prove that ...”"], look={"style": "italic"})
    p.card(THIRDS[2], y, 280, h, "opens for any prover", ["no operator and no judge,", "even if the sealer is gone"], bar=GREEN)
    for x in THIRDS[1:]:
        p.arrow(x - 16, y + h / 2, x - 5, y + h / 2)

    p.band(LEFT, 184, FULL, "The same lock, with six different rules", fill=DARK, h=36)
    cards = [
        ("MODELLED HERE", "Bridge without a custodian",
         ["“these tokens were burned on Solana,", "and I know the secret behind the burn”"],
         "bridge_sim/, with a simulated lock"),
        ("MODELLED HERE", "Time lock",
         ["“Bitcoin has grown 52,560 blocks", "past block X” (about a year)"],
         "bridge_sim/timelock.py, with a simulated lock"),
        ("TOY DEMO HERE", "Reward for a solution",
         ["“here is a solution to this puzzle”", "(in the demo: two factors of 47,053)"],
         "demo_lock.py, with the real lock at toy size"),
        ("NOT CODED", "Sending to a credential",
         ["“I hold a certificate from this registry", "that says: licensed doctor”"],
         "needs a rule that checks a signature"),
        ("NOT CODED", "Paying on Bitcoin for a proof",
         ["“this program ran on this input", "and gave this result”"],
         "the proof checker is the building block"),
        ("NOT CODED", "Spending rules on Bitcoin",
         ["“this spend follows the rule", "the coins were sealed under”"],
         "called covenants; needs extra design"),
    ]
    for i, (state, title, rule, where) in enumerate(cards):
        x, y = THIRDS[i % 3], 230 + 162 * (i // 3)
        p.card(x, y, 280, 150, title, ["Opens for whoever can prove:"], bar=STATUS[state][3], look={"size": 11.5})
        p.lines(x + 22, y + 66, rule, fill=INK, style="italic")
        p.text(x + 22, y + 107, where, size=11.5)
        p.tag(x + 22, y + 133, state)
    p.strip(554, "Only the rule changes",
            "The lock and the proof checker are the same for every use. None of it runs at real size here.")
    p.save("uses.svg")


# ---------------------------------------------------------------- 5. from a rule to a lock

def pipeline():
    p = Picture(660, "From a rule to a lock: what is built here",
                "Every use goes through the same five stages. Stage 1, the rule as a program: two rules are written "
                "in plain Python, the bridge's and a small time lock. Stage 2, a proof that the program said yes: "
                "not coded. Stage 3, one small Groth16 proof: a toy system that makes real test proofs. Stage 4, the "
                "proof checker as lock gates: built, with gaps, 1.4 times the size the paper reports. Stage 5, the "
                "lock: built at toy size.")
    p.band(LEFT, 20, FULL, "From a rule to a lock: what is built here", h=44)
    p.band(LEFT, 76, 430, "The five stages", fill=DARK, h=34)
    p.band(RIGHT, 76, 430, "In this repository", fill=DARK, h=34)
    stages = [
        ("The rule", ["A program that looks at evidence", "and answers yes or no."],
         "TWO RULES WRITTEN", ["The bridge's rule and a small time lock,", "in plain Python (bridge_sim/)."]),
        ("Proof of the run", ["A zero-knowledge machine proves that", "the program said yes."],
         "NOT CODED", ["The paper uses an existing machine.", "A rule would be rewritten for it."]),
        ("One small proof", ["That proof is wrapped into a single", "Groth16 proof of fixed size."],
         "TOY SIZE", ["A small Groth16 system that makes real", "test proofs (encoding/groth16.py)."]),
        ("Proof checker as lock gates", ["“This proof is valid”, rewritten as", "equations over bits."],
         "BUILT, WITH GAPS", ["Checks real proofs, at 1.4× the size the", "paper reports (praxis_lock/encoding/)."]),
        ("The lock", ["A key sealed so that only a solution", "of those equations opens it."],
         "TOY SIZE", ["The real scheme, on small statements.", "Full size, about 8 GB a key piece, does not run."]),
    ]
    top, h, pitch = 120, 78, 90
    for i, (title, what, state, here) in enumerate(stages):
        y = top + i * pitch
        p.card(LEFT, y, 430, h, title, what, number=str(i + 1))
        p.add(f'<rect x="{RIGHT}" y="{y}" width="430" height="{h}" fill="{SHADE}"/>')
        p.add(f'<rect x="{RIGHT}" y="{y}" width="6" height="{h}" fill="{STATUS[state][3]}"/>')
        p.tag(RIGHT + 22, y + 27, state)
        p.lines(RIGHT + 22, y + 50, here, fill=INK)
        p.arrow(LEFT + 430 + 4, y + h / 2, RIGHT - 5, y + h / 2, colour=GREY)
    p.strip(top + 5 * pitch, "Stages 2 to 5 are the same for every use", "A new use needs only a new rule in stage 1.")
    p.save("pipeline.svg")


# ---------------------------------------------------------------- 6. three other locks, step by step

def examples():
    p = Picture(574, "Three other locks, step by step",
                "Each follows the bridge's pattern: seal a secret to a rule, wait until the rule can be proven, open. "
                "Time lock (modelled here): sealed to Bitcoin growing by a number of blocks; anyone opens it by "
                "showing the block headers. Reward (toy demo here): sealed to a solution of a puzzle; the solver "
                "opens it. Credential (not coded): sealed to holding a certificate; any holder opens it.")
    p.band(LEFT, 20, FULL, "Three other locks, step by step", h=44)
    rows = [
        ("Time lock", "MODELLED HERE",
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
    top, h, pitch = 76, 82, 140
    for i, (title, state, seal, then, opened) in enumerate(rows):
        y = top + i * pitch
        p.band(LEFT, y, FULL, title, fill=DARK if state != "NOT CODED" else GREY, h=34, aside=state)
        for j, (label, body) in enumerate((("Seal", seal), ("Then", then), ("Open", opened))):
            x = THIRDS[j]
            p.card(x, y + 44, 280, h, label, body, bar=GREEN if label == "Open" else RED)
            if j:
                p.arrow(x - 16, y + 44 + h / 2, x - 5, y + 44 + h / 2)
    p.strip(top + 3 * pitch - 2, "Limits shared by all of them",
            "The rule is fixed when the lock is made, and whoever holds a proof can open.")
    p.save("examples.svg")


# ---------------------------------------------------------------- 7. what every use has to live with

def limits():
    p = Picture(392, "What every use has to live with",
                "Six limits. The rule is fixed when the lock is made. Whoever has a proof opens it, once and for "
                "all. Evidence can be forged at a price. Each sealed key piece is about 8 GB of public data in the "
                "paper and about 11 GB with this repository's encoding. One setup stands behind every lock. It is "
                "not safe against quantum computers and has not been reviewed.")
    p.band(LEFT, 20, FULL, "What every use has to live with", h=44)
    cards = [
        ("The rule is fixed",
         ["It is set when the lock is made and", "cannot be changed or withdrawn.", "It sees only what a proof shows it."]),
        ("Whoever has a proof opens",
         ["The lock does not know who anyone", "is. Public evidence means a public", "secret. Opening is once and for all."]),
        ("Evidence can be forged",
         ["At a price. A time lock is as strong", "as the cost of mining its blocks in", "secret; a signature rule, as its keys."]),
        ("Large and heavy",
         ["About 8 GB of public data for each", "sealed key piece in the paper, about", "11 GB here. Heavy to make and open."]),
        ("One setup for every lock",
         ["Whoever ran the proof system's setup", "and kept its secret numbers can open", "every lock made for that key."]),
        ("New and unreviewed",
         ["The paper is from 2026 and has not", "been reviewed. The scheme is not", "safe against quantum computers."]),
    ]
    for i, (title, rows) in enumerate(cards):
        p.card(THIRDS[i % 3], 78 + 116 * (i // 3), 280, 102, title, rows, bar=GREY, number=str(i + 1))
    p.strip(312, "On top of these, this repository has no security at all",
            "The lock in bridge_sim/ is simulated and praxis_lock/ runs at toy size.")
    p.save("limits.svg")


# ---------------------------------------------------------------- 8. the seven steps, as a map for the list in chapter 3

def steps():
    p = Picture(370, "The seven steps of the bridge",
                "On Bitcoin: 1 seal empty vaults, 2 deposit Bitcoin, 7 the vault opens for the burner. On Solana: "
                "3 mint bridged Bitcoin, 4 launch a coin, 5 trade priced in Bitcoin, 6 burn with a secret mark. "
                "Proof of deposit leads from step 2 to step 3. Proof of the burn plus the secret leads from step 6 "
                "to step 7. Between deposit and opening the Bitcoin stays sealed in the vault.")
    p.band(LEFT, 20, FULL, "The seven steps of the bridge", h=44)
    w, h, top, low = 136, 58, 82, 214
    cols = [160, 316, 472, 628, 784]

    def node(x, y, number, rows, bar=RED):
        p.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{SHADE}"/>')
        p.add(f'<rect x="{x}" y="{y}" width="6" height="{h}" fill="{bar}"/>')
        p.add(f'<rect x="{x + 16}" y="{y + 18}" width="22" height="22" fill="{bar}"/>')
        p.text(x + 27, y + 33.5, number, fill=PAPER, weight=700, anchor="middle")
        p.lines(x + 47, y + 26, rows, leading=16, fill=INK)

    p.band(LEFT, top + 14, 110, "On Bitcoin", fill=DARK, h=30)
    node(cols[0], top, "1", ["Seal empty", "vaults"])
    node(cols[1], top, "2", ["Deposit", "Bitcoin"])
    node(cols[4], top, "7", ["Vault opens", "for the burner"], bar=GREEN)
    p.arrow(cols[0] + w + 3, top + h / 2, cols[1] - 4, top + h / 2)
    p.add(f'<line x1="{cols[1] + w + 8}" y1="{top + h / 2}" x2="{cols[4] - 8}" y2="{top + h / 2}" stroke="{LINE}" '
          f'stroke-width="1.6" stroke-dasharray="2 5"/>')
    p.text((cols[1] + w + cols[4]) / 2, top + h / 2 - 9, "the Bitcoin stays sealed in the vault", style="italic", anchor="middle")

    p.band(LEFT, low + 14, 110, "On Solana", fill=DARK, h=30)
    for x, number, rows in ((cols[1], "3", ["Mint bridged", "Bitcoin"]), (cols[2], "4", ["Launch", "a coin"]),
                            (cols[3], "5", ["Trade, priced", "in Bitcoin"]), (cols[4], "6", ["Burn with a", "secret mark"])):
        node(x, low, number, rows)
    for i in (1, 2, 3):
        p.arrow(cols[i] + w + 3, low + h / 2, cols[i + 1] - 4, low + h / 2)

    p.arrow(cols[1] + w / 2, top + h + 5, cols[1] + w / 2, low - 6)
    p.arrow(cols[4] + w / 2, low - 5, cols[4] + w / 2, top + h + 6)
    between = (top + h + low) / 2 + 4.5
    p.text(cols[1] + w / 2 + 12, between, "proof of deposit", style="italic")
    p.text(cols[4] + w / 2 - 12, between, "proof of burn plus secret", style="italic", anchor="end")
    p.strip(290, "The numbers are those of the steps in the text",
            "Two proofs join the chains: of the deposit on the way in, and of the burn on the way out.")
    p.save("steps.svg")


if __name__ == "__main__":
    for draw in (solves, use_cases, far_future, overview, uses, pipeline, examples, limits, steps):
        draw()
    print("wrote", ", ".join(sorted(f.name for f in HERE.glob("*.svg"))))
