# Contributing

The most useful contribution is an attack that works.

## Break it

1. Read the rule in `bridge_sim/lock.py` (`BurnStatement`) and the trust assumptions
   in the README.
2. Write your attack as a test. `tests/test_bridge.py` has helpers for forging
   validator signatures and mining a private Bitcoin fork.
3. If the attack succeeds without breaking a stated assumption, open an issue with
   the "I broke it" template. That is a real finding.

Attacks that only work by breaking a stated assumption are still welcome if they
show the assumption is weaker or more expensive than the README says.

## Check the lock

`praxis_lock/` was transcribed from the Lean definitions, not from the paper's text.

- Compare it against the paper and report any place the two disagree.
- Find a statement, parameter set or coin choice where a true solution fails to
  decrypt. The Lean proof says that happens with probability at most 2/q.
- Find a way to decrypt on the BN254 backend without a solution that is cheaper
  than trying every answer. At toy sizes this says little about the scheme, but it
  would expose a transcription error.

## Check the encoding

`praxis_lock/encoding/` writes a Groth16 proof checker as lock gates. The paper
reports the size of its own encoding but not how it is built, so this one was
written from first principles.

- Find an assignment that satisfies the verifier gates for a statement that is
  false. Two routes are already known and listed in the README (a second proof
  point outside the right subgroup, and point additions with an undefined slope);
  a working exploit of either is still worth having as a test. Anything else is a
  real finding.
- Make it smaller. The paper reports 391,693 bits and 1,692 constraints; this
  encoding uses about 8.5 million bits. `Circuit.stages` shows where they go, and
  the README lists two steps not yet taken: wide gates, and folding the root check
  into the pairing loop.
- If the authors publish their encoding, compare the two and describe the
  differences.

## Improve it

- Model something the README lists as not modelled: Bitcoin reorganisations, fees,
  smaller vault sizes, re-sealing vaults.
- Port a piece to a real test network while keeping the lock simulated.
- Write the vault rule as a zero-knowledge program.

## Ground rules

- Every safety check needs a test that fails when the check is removed.
- Tests that document a weakness are named `ASSUMPTION`, `KNOWN_LIMIT` or `WARNING`.
- No dependencies beyond the Python standard library, except the optional `py_ecc`
  for the BN254 backend of `praxis_lock/` and for the pairing parts of the encoding.
- A value the prover supplies inside the encoding goes through `Circuit.hint`
  under the label of the gate that checks it, so the tests can swap in a wrong one.
- Nothing here may be presented as production-ready or safe for real funds.
