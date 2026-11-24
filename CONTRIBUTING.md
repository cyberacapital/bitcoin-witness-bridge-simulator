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

## Improve it

- Model something the README lists as not modelled: Bitcoin reorganisations, fees,
  smaller vault sizes, re-sealing vaults.
- Port a piece to a real test network while keeping the lock simulated.
- Write the vault rule as a zero-knowledge program.

## Ground rules

- Every safety check needs a test that fails when the check is removed.
- Tests that document a weakness are named `ASSUMPTION`, `KNOWN_LIMIT` or `WARNING`.
- No dependencies beyond the Python standard library, except the optional `py_ecc`
  for the BN254 backend of `praxis_lock/`.
- Nothing here may be presented as production-ready or safe for real funds.
