"""A narrated run of the bridge model. Run with:  python demo.py"""
from bridge_sim import COIN, Key, LaunchError, LockError, TxOut, World, open_vault


def btc(sats: int) -> str:
    return f"{sats / COIN:.2f} BTC"


def main() -> None:
    print("Setting up a mini Bitcoin, a mini Solana with 4 validators, and the bridge.\n")
    w = World()
    alice = w.funded_user(COIN)
    bob = Key.generate()
    mallory = Key.generate()

    vault, _ = w.seal_and_register()
    gib = vault.storage_bytes / 2**30
    print(f"1. SEAL     Three setup members seal vault #{vault.vault_id} for {btc(vault.denom)}.")
    print(f"            Nobody holds its key. In reality that is {gib:.1f} GiB of locked files.")

    w.deposit_and_mint(alice, vault)
    print(f"2. DEPOSIT  Alice sends {btc(vault.denom)} into the vault on Bitcoin and waits 6 blocks.")
    print(f"3. MINT     Solana checks the deposit and mints. Alice holds {btc(w.bridge.balances[alice.pub])} in tokens.")

    # Bob bridges 0.2 BTC of his own so he has something to trade with.
    bob_btc = w.funded_user(COIN)
    for _ in range(2):
        extra, _ = w.seal_and_register()
        w.deposit_and_mint(bob_btc, extra)
    w.bridge.transfer(bob_btc, bob.pub, 2 * vault.denom)

    pad = w.launchpad
    pad.launch(alice, "MOON", 1_000_000, btc_liquidity=vault.denom)
    print(f"4. LAUNCH   Alice launches MOON: 1,000,000 coins pooled against her {btc(vault.denom)}, liquidity locked.")
    print(f"            Starting price: {float(pad.price('MOON')):.2f} sats per coin.")

    spent = vault.denom // 2
    coins = pad.buy(bob, "MOON", spent)
    print(f"5. TRADE    Bob (who bridged {btc(2 * vault.denom)}) buys {coins:,} MOON for {btc(spent)}.")
    print(f"            Price is now {float(pad.price('MOON')):.2f} sats per coin.")
    back = pad.sell(bob, "MOON", coins)
    print(f"            Bob sells them all and gets {back / COIN:.4f} BTC back; the difference is fees.")
    try:
        pad.withdraw_liquidity(alice, "MOON")
        print("   ATTACK   Alice pulled the pool. This should never print.")
    except LaunchError:
        print("   ATTACK   Alice tries to pull the pool's Bitcoin: refused, the liquidity is locked.")
    pad.check_invariant()

    record, secret = w.burn(bob)
    vault = w.vaults[record.vault_id]
    root, anchor = w.finalize_and_anchor()
    print(f"6. BURN     Bob burns {btc(vault.denom)} of tokens with a secret mark. Solana assigns him vault #{record.vault_id}.")
    print("            The burn is finalised and its fingerprint is written into Bitcoin.")

    try:
        open_vault(vault, w.witness(vault, record, b"mallory guesses", root, anchor))
        print("   ATTACK   Mallory opened the vault. This should never print.")
    except LockError:
        print("   ATTACK   Mallory copies Bob's public proof but lacks the secret: vault stays shut.")

    key = open_vault(vault, w.witness(vault, record, secret, root, anchor))
    w.btc.spend(w.bridge.vaults[vault.vault_id].deposit, key, [TxOut(bob.pub, vault.denom)])
    print(f"7. OPEN     Bob's proof plus his secret opens the vault. Bob holds {btc(w.btc.balance(bob.pub))} of native Bitcoin.")

    w.bridge.check_invariant(w.btc)
    in_pool = w.bridge.balances[pad.address]
    print(f"\nRule check: tokens in circulation = {btc(w.bridge.supply)}, matching the Bitcoin left in vaults.")
    print(f"            Of that, {in_pool / COIN:.4f} BTC sits in the MOON pool, still backed one-for-one.")
    print(f"Mallory ends with {btc(w.btc.balance(mallory.pub))}.")


if __name__ == "__main__":
    main()
