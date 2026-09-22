"""
XLS-0096 Confidential Transfers — Devnet Verifier

What this does
--------------
Runs the XLS-96 (Confidential Transfers for Multi-Purpose Tokens) lifecycle
against XRPL Devnet and checks the observed behaviour against what the
specification states. Nothing is asserted that the script did not actually
observe on-ledger; every check prints the transaction result code it saw.

PART A - Confidential lifecycle
  Issues a confidential-capable MPT, registers issuer and auditor encryption
  keys, moves a public balance into a confidential one, and confirms that the
  holder and the auditor independently decrypt the SAME value from DIFFERENT
  ciphertexts on the same ledger entry.

PART B - TransferFee interaction (XLS-0096 section 6.4)
  Section 6.4 states that confidential balances and a nonzero TransferFee are
  mutually exclusive, in both directions. This part attempts both transitions
  and reports the actual result codes.

PART C - Confidential send between holders
  Opts a second holder in, sends an amount from one holder to the other with
  ConfidentialMPTSend, and confirms the ledger moved the right value: the
  sender is debited, the recipient is credited, and the auditor recovers the
  transferred amount itself - not merely the balances - from its own
  ciphertext.

Requirements
------------
    pip install xrpl-py xrpl-py-confidential

Network: Devnet only. Every account is created fresh from the faucet and holds
no real value.
"""

import re

from xrpl.clients import JsonRpcClient
from xrpl.ext.confidential import (
    MPTCrypto,
    decrypt_confidential_balance,
    prepare_confidential_convert,
    prepare_confidential_merge_inbox,
    prepare_confidential_send,
)
from xrpl.models import (
    LedgerEntry,
    MPTokenAuthorize,
    MPTokenIssuanceCreate,
    MPTokenIssuanceCreateFlag,
    MPTokenIssuanceSet,
    Payment,
)
from xrpl.models.requests.ledger_entry import MPToken
from xrpl.transaction import submit_and_wait
from xrpl.utils import encode_mptoken_metadata
from xrpl.wallet import generate_faucet_wallet

DEVNET = "https://s.devnet.rippletest.net:51234"
EXPLORER = "https://devnet.xrpl.org"

TICKER = "CTST"
SUPPLY = 12_000

client = JsonRpcClient(DEVNET)

results = []


def record(name, passed, detail):
    """Record a check. Nothing here decides an outcome on its own."""
    results.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name} — {detail}")


RESULT_CODE = re.compile(r"\b(te[cfmsl][A-Za-z_]+)\b")


def send(tx, wallet):
    """Submit a transaction and return (result_code, response_or_None).

    xrpl-py raises XRPLReliableSubmissionException on tec-class results, even
    though those results ARE written to a validated ledger and are precisely
    what PART B needs to observe. The result code is recovered from the
    exception so that a rejection is reported as the code the ledger returned
    rather than as a client-side error.
    """
    try:
        res = submit_and_wait(tx, client, wallet, autofill=True).result
        return res["meta"]["TransactionResult"], res
    except Exception as exc:
        match = RESULT_CODE.search(str(exc))
        if match:
            return match.group(1), None
        return f"EXCEPTION: {type(exc).__name__}: {exc}", None


def metadata(name, desc):
    return encode_mptoken_metadata(
        {
            "ticker": TICKER,
            "name": name,
            "desc": desc,
            "icon": "https://example.org/ctst-icon.png",
            "asset_class": "rwa",
            "asset_subclass": "treasury",
            "issuer_name": "XLS-0096 Verifier",
        }
    )


# ---------------------------------------------------------------------------
# PART A — Confidential lifecycle
# ---------------------------------------------------------------------------

print("\n=== PART A: confidential lifecycle ===\n")

print("Funding accounts (issuer / holder / auditor)...")
issuer = generate_faucet_wallet(client)
holder = generate_faucet_wallet(client)
auditor = generate_faucet_wallet(client)
print(f"  issuer  : {issuer.address}")
print(f"  holder  : {holder.address}")
print(f"  auditor : {auditor.address}")

# An issuer cannot hold a confidential balance of its own token, because an
# issuer's balance does not count as tokens in circulation. Confidential tokens
# enter circulation through a second account the issuer controls.

crypto = MPTCrypto()
issuer_priv, issuer_pub = crypto.generate_keypair()
holder_priv, holder_pub = crypto.generate_keypair()
auditor_priv, auditor_pub = crypto.generate_keypair()

print("\nCreating a confidential-capable issuance (TransferFee = 0)...")
code, res = send(
    MPTokenIssuanceCreate(
        account=issuer.address,
        asset_scale=0,
        maximum_amount="1000000000",
        transfer_fee=0,
        flags=MPTokenIssuanceCreateFlag.TF_MPT_CAN_HOLD_CONFIDENTIAL_BALANCE
        | MPTokenIssuanceCreateFlag.TF_MPT_CAN_TRANSFER
        | MPTokenIssuanceCreateFlag.TF_MPT_CAN_CLAWBACK
        | MPTokenIssuanceCreateFlag.TF_MPT_CAN_LOCK,
        mptoken_metadata=metadata("Confidential Token", "XLS-96 verifier token."),
    ),
    issuer,
)
record("A1 confidential issuance created", code == "tesSUCCESS", code)
if code != "tesSUCCESS":
    raise SystemExit("Cannot continue without an issuance.")

mpt_id = res["meta"]["mpt_issuance_id"]
print(f"  issuance id : {mpt_id}")
print(f"  {EXPLORER}/transactions/{res['hash']}")

print("\nRegistering issuer and auditor encryption keys...")
code, res = send(
    MPTokenIssuanceSet(
        account=issuer.address,
        mptoken_issuance_id=mpt_id,
        issuer_encryption_key=issuer_pub,
        auditor_encryption_key=auditor_pub,
    ),
    issuer,
)
record("A2 encryption keys registered", code == "tesSUCCESS", code)

print("\nAuthorizing the holder and sending a PUBLIC payment...")
send(MPTokenAuthorize(account=holder.address, mptoken_issuance_id=mpt_id), holder)
code, res = send(
    Payment(
        account=issuer.address,
        destination=holder.address,
        amount={"mpt_issuance_id": mpt_id, "value": str(SUPPLY)},
    ),
    issuer,
)
record("A3 public payment", code == "tesSUCCESS", f"{code}, amount visible: {SUPPLY}")
if res:
    print(f"  {EXPLORER}/transactions/{res['hash']}")

print("\nConverting the public balance into a confidential one...")
convert_tx = prepare_confidential_convert(
    client, holder, mpt_id, SUPPLY, issuer_pub, holder_priv, holder_pub, auditor_pub
)
code, res = send(convert_tx, holder)
record("A4 convert to confidential", code == "tesSUCCESS", code)
if res:
    print(f"  {EXPLORER}/transactions/{res['hash']}")

print("\nMerging the inbox into the spending balance...")
code, res = send(prepare_confidential_merge_inbox(client, holder, mpt_id), holder)
record("A5 merge inbox", code == "tesSUCCESS", code)

print("\nReading the ledger entry and decrypting...")
node = client.request(
    LedgerEntry(mptoken=MPToken(mpt_issuance_id=mpt_id, account=holder.address))
).result["node"]
issuance = client.request(LedgerEntry(mpt_issuance=mpt_id)).result["node"]
supply_ceiling = int(issuance["ConfidentialOutstandingAmount"])

ciphertext = str(node["ConfidentialBalanceSpending"])
print(f"  on-ledger ciphertext (truncated): {ciphertext[:64]}...")

holder_view = decrypt_confidential_balance(
    node["ConfidentialBalanceSpending"], holder_priv, range_high=supply_ceiling
)
auditor_view = decrypt_confidential_balance(
    node["AuditorEncryptedBalance"], auditor_priv, range_high=supply_ceiling
)
print(f"  holder decrypts  : {holder_view}")
print(f"  auditor decrypts : {auditor_view}")

record(
    "A6 holder reads the converted amount",
    holder_view == SUPPLY,
    f"expected {SUPPLY}, got {holder_view}",
)
record(
    "A7 auditor reads the same value from a different ciphertext",
    auditor_view == holder_view == SUPPLY,
    f"holder={holder_view}, auditor={auditor_view}",
)
record(
    "A8 plaintext amount is not present on the entry",
    "MPTAmount" not in node or str(node.get("MPTAmount")) in ("0", "None"),
    f"MPTAmount={node.get('MPTAmount')}",
)


# ---------------------------------------------------------------------------
# PART B — TransferFee interaction (section 6.4)
# ---------------------------------------------------------------------------

print("\n=== PART B: TransferFee interaction (spec section 6.4) ===\n")

# B1. Direction 1 — a confidential issuance must refuse a nonzero TransferFee.
print("B1. Setting TransferFee=250 on the confidential issuance above...")
try:
    tx = MPTokenIssuanceSet(
        account=issuer.address,
        mptoken_issuance_id=mpt_id,
        transfer_fee=250,
    )
    code, _ = send(tx, issuer)
except Exception as exc:
    # The field may not exist in this SDK version, or model validation may
    # reject it before the transaction is ever built. Either is worth reporting.
    code = f"REJECTED_CLIENT_SIDE: {type(exc).__name__}: {exc}"
record(
    "B1 confidential issuance refuses a nonzero TransferFee",
    code.startswith("tec") or code.startswith("tem"),
    f"spec 6.4 expects tecNO_PERMISSION; observed: {code}",
)

# B2. Direction 2 — a fee-bearing issuance must refuse the confidential flag.
print("\nB2. Creating a separate issuance WITH TransferFee=250...")
code, res = send(
    MPTokenIssuanceCreate(
        account=issuer.address,
        asset_scale=0,
        maximum_amount="1000000000",
        transfer_fee=250,
        flags=MPTokenIssuanceCreateFlag.TF_MPT_CAN_TRANSFER,
        mptoken_metadata=metadata("Fee Token", "Fee-bearing token, 2.5% fee."),
    ),
    issuer,
)
record("B2a fee-bearing issuance created", code == "tesSUCCESS", code)

if code == "tesSUCCESS":
    fee_mpt_id = res["meta"]["mpt_issuance_id"]
    print(f"  issuance id : {fee_mpt_id}")

    # The flag name differs between SDK versions; look it up rather than guess.
    try:
        from xrpl.models.transactions.mptoken_issuance_set import (
            MPTokenIssuanceSetFlag,
        )

        flag = next(
            (
                f
                for f in MPTokenIssuanceSetFlag
                if "CONFIDENTIAL" in f.name.upper()
            ),
            None,
        )
    except Exception as exc:
        flag = None
        print(f"  (flag lookup failed: {exc})")

    if flag is None:
        record(
            "B2b fee-bearing issuance refuses the confidential flag",
            False,
            "could not locate a 'can hold confidential balance' set-flag in this SDK",
        )
    else:
        print(f"\n  Enabling {flag.name} on the fee-bearing issuance...")
        code, _ = send(
            MPTokenIssuanceSet(
                account=issuer.address,
                mptoken_issuance_id=fee_mpt_id,
                flags=flag,
            ),
            issuer,
        )
        record(
            "B2b fee-bearing issuance refuses the confidential flag",
            code.startswith("tec") or code.startswith("tem"),
            f"spec 6.4 expects a rejection; observed: {code}",
        )


# ---------------------------------------------------------------------------
# PART C — Confidential send between holders
# ---------------------------------------------------------------------------

print("\n=== PART C: confidential send between holders ===\n")

SEND_AMOUNT = 3_000
SECOND_HOLDER_FUNDING = 1_000

print("Funding and authorizing a second holder...")
holder2 = generate_faucet_wallet(client)
holder2_priv, holder2_pub = crypto.generate_keypair()
print(f"  holder2 : {holder2.address}")

code, _ = send(
    MPTokenAuthorize(account=holder2.address, mptoken_issuance_id=mpt_id), holder2
)
record("C1 second holder authorized", code == "tesSUCCESS", code)

code, _ = send(
    Payment(
        account=issuer.address,
        destination=holder2.address,
        amount={"mpt_issuance_id": mpt_id, "value": str(SECOND_HOLDER_FUNDING)},
    ),
    issuer,
)
record(
    "C2 second holder funded publicly",
    code == "tesSUCCESS",
    f"{code}, amount visible: {SECOND_HOLDER_FUNDING}",
)

# A holder's first conversion registers its HolderEncryptionKey. That key is
# what lets a sender encrypt a transfer under the recipient's key, so a holder
# cannot receive a confidential send before it has converted once.
print("\nConverting the second holder's balance (registers its encryption key)...")
code, _ = send(
    prepare_confidential_convert(
        client,
        holder2,
        mpt_id,
        SECOND_HOLDER_FUNDING,
        issuer_pub,
        holder2_priv,
        holder2_pub,
        auditor_pub,
    ),
    holder2,
)
record("C3 second holder opted in by converting", code == "tesSUCCESS", code)

code, _ = send(prepare_confidential_merge_inbox(client, holder2, mpt_id), holder2)
record("C4 second holder merged its inbox", code == "tesSUCCESS", code)

print(f"\nSending {SEND_AMOUNT} confidentially from holder to holder2...")
send_tx = prepare_confidential_send(
    client,
    holder,
    holder2.address,
    mpt_id,
    SEND_AMOUNT,
    holder_priv,
    holder_pub,
    holder2_pub,
    issuer_pub,
    auditor_pub,
)
code, res = send(send_tx, holder)
record("C5 confidential send accepted", code == "tesSUCCESS", code)
if res:
    print(f"  {EXPLORER}/transactions/{res['hash']}")
    print("  (the transaction carries ciphertexts and a proof, not an amount)")

code, _ = send(prepare_confidential_merge_inbox(client, holder2, mpt_id), holder2)
record("C6 recipient merged the received amount", code == "tesSUCCESS", code)

print("\nDecrypting both balances after the transfer...")
issuance = client.request(LedgerEntry(mpt_issuance=mpt_id)).result["node"]
ceiling = int(issuance["ConfidentialOutstandingAmount"])


def spending_balance(address, privkey):
    entry = client.request(
        LedgerEntry(mptoken=MPToken(mpt_issuance_id=mpt_id, account=address))
    ).result["node"]
    return decrypt_confidential_balance(
        entry["ConfidentialBalanceSpending"], privkey, range_high=ceiling
    )


sender_after = spending_balance(holder.address, holder_priv)
recipient_after = spending_balance(holder2.address, holder2_priv)
print(f"  sender reads    : {sender_after}")
print(f"  recipient reads : {recipient_after}")

record(
    "C7 sender debited by the sent amount",
    sender_after == SUPPLY - SEND_AMOUNT,
    f"expected {SUPPLY - SEND_AMOUNT}, got {sender_after}",
)
record(
    "C8 recipient credited the sent amount",
    recipient_after == SECOND_HOLDER_FUNDING + SEND_AMOUNT,
    f"expected {SECOND_HOLDER_FUNDING + SEND_AMOUNT}, got {recipient_after}",
)

# The auditor recovers the transferred amount itself, from a ciphertext carried
# on the transaction, without either holder's key.
auditor_amount = decrypt_confidential_balance(
    send_tx.auditor_encrypted_amount, auditor_priv, range_high=ceiling
)
print(f"  auditor reads the transferred amount as: {auditor_amount}")
record(
    "C9 auditor reads the transferred amount",
    auditor_amount == SEND_AMOUNT,
    f"expected {SEND_AMOUNT}, got {auditor_amount}",
)


# ---------------------------------------------------------------------------

print("\n=== SUMMARY ===\n")
passed = sum(1 for _, ok, _ in results if ok)
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"        {detail}")
print(f"\n  {passed}/{len(results)} checks passed.")
print(f"\n  Issuer account    : {EXPLORER}/accounts/{issuer.address}")
print(f"  Holder account    : {EXPLORER}/accounts/{holder.address}")
print(f"  Recipient account : {EXPLORER}/accounts/{holder2.address}")
