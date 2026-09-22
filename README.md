# XLS-0096 Confidential Transfers — Devnet Verifier

A single Python script that runs the
[XLS-0096 (Confidential Transfers for Multi-Purpose Tokens)](https://xls.xrpl.org/xls/XLS-0096-confidential-mpt.html)
lifecycle against XRPL Devnet and checks the observed behaviour against what
the specification states.

Nothing is asserted that the script did not actually observe on-ledger. Every
check prints the transaction result code the ledger returned, so a reader can
see what happened rather than take the script's word for it.

## What it checks

**Part A — confidential lifecycle**

| # | Check |
|---|---|
| A1 | A confidential-capable MPT issuance can be created with `TransferFee = 0` |
| A2 | Issuer and auditor encryption keys register on the issuance |
| A3 | A public payment funds the holder (the amount is visible to everyone) |
| A4 | `ConfidentialMPTConvert` moves that balance into a confidential one |
| A5 | `ConfidentialMPTMergeInbox` folds the inbox into the spending balance |
| A6 | The holder decrypts its own balance and gets the converted amount |
| A7 | The auditor decrypts a **different ciphertext** on the same entry and gets the **same value** |
| A8 | No plaintext amount remains on the `MPToken` entry |

A7 is the point of the whole design: one balance, stored once per registered
key, readable only by the party holding the matching private key, and
consistent across all of them.

**Part B — TransferFee interaction (spec section 6.4)**

Section 6.4 states that confidential balances and a nonzero `TransferFee` are
mutually exclusive in both directions. Part B attempts both transitions and
reports what the ledger returns.

| # | Check |
|---|---|
| B1 | A confidential issuance refuses a nonzero `TransferFee` |
| B2a | A fee-bearing issuance (2.5%) can be created normally |
| B2b | That fee-bearing issuance refuses the confidential-balance flag |

**Part C — confidential send between holders**

| # | Check |
|---|---|
| C1 | A second holder is authorized to hold the MPT |
| C2 | The issuer funds it with a public payment (amount visible) |
| C3 | Its first `ConfidentialMPTConvert` registers its `HolderEncryptionKey` |
| C4 | It merges that balance into its spending balance |
| C5 | `ConfidentialMPTSend` moves an amount from one holder to the other |
| C6 | The recipient merges what it received |
| C7 | The sender's decrypted balance fell by exactly the sent amount |
| C8 | The recipient's decrypted balance rose by exactly the sent amount |
| C9 | The auditor recovers the **transferred amount itself**, from a ciphertext on the transaction, without either holder's key |

C3 is a constraint worth knowing before designing around this: a holder that
has never converted has no registered encryption key, so nobody can encrypt a
transfer to it. Receiving a confidential send requires opting in first.

C9 is the compliance story in one line. C7 and C8 show the ledger moved the
right value while the transaction itself carried no plaintext amount.

## Result

As of the run recorded below, **20/20 checks passed** on Devnet:

```
PASS  A7 auditor reads the same value from a different ciphertext
      holder=12000, auditor=12000
PASS  A8 plaintext amount is not present on the entry
      MPTAmount=None
PASS  B1 confidential issuance refuses a nonzero TransferFee
      spec 6.4 expects tecNO_PERMISSION; observed: tecNO_PERMISSION
PASS  B2b fee-bearing issuance refuses the confidential flag
      spec 6.4 expects a rejection; observed: tecNO_PERMISSION
PASS  C7 sender debited by the sent amount
      expected 9000, got 9000
PASS  C8 recipient credited the sent amount
      expected 4000, got 4000
PASS  C9 auditor reads the transferred amount
      expected 3000, got 3000

20/20 checks passed.
```

Because enabling confidential balances is one-way, B1 and B2b together mean an
issuer chooses between confidentiality and native transfer fees **permanently**,
in whichever order the choice is made. That observation is the basis of
[XRPL-Standards Discussion #644](https://github.com/XRPLF/XRPL-Standards/discussions/644).

## Running it

```bash
pip install xrpl-py xrpl-py-confidential
python XLS-0096-Verifier.py
```

Takes roughly six minutes. It funds four fresh Devnet accounts from the faucet,
so no configuration and no existing wallet are needed.

### In Google Colab

Colab already has an event loop running, and `xrpl-py`'s synchronous helpers
call `asyncio.run()` internally, which cannot nest. Run this first:

```python
!pip install -q xrpl-py xrpl-py-confidential nest_asyncio
import nest_asyncio
nest_asyncio.apply()
```

Then upload the script and run it with `%run XLS-0096-Verifier.py`.

## Notes

- **Devnet only.** Every account is created fresh from the faucet and holds no
  real value. Devnet is reset periodically, so explorer links from old runs
  eventually stop resolving — rerun the script to generate current ones.
- `ConfidentialTransfer` is an amendment still open for voting, so this
  behaviour is not available on Mainnet.
- `xrpl-py` raises `XRPLReliableSubmissionException` on `tec`-class results
  even though those results are written to a validated ledger. The script
  recovers the result code from the exception so that a rejection is reported
  as the code the ledger returned rather than as a client-side error.
- Confidential transactions cost ten times the standard transaction cost.

## References

- [XLS-0096 specification](https://xls.xrpl.org/xls/XLS-0096-confidential-mpt.html)
- [Discussion #644 — the TransferFee question this repo backs](https://github.com/XRPLF/XRPL-Standards/discussions/644)
- [Discussion #372 — the original XLS-96 thread (closed)](https://github.com/XRPLF/XRPL-Standards/discussions/372)
- [Issue an MPT for Confidential Transfers (xrpl.org tutorial)](https://xrpl.org/docs/tutorials/tokens/mpts/issue-mpt-for-confidential-transfers)
- [Send Confidential MPT Payments (xrpl.org tutorial)](https://xrpl.org/docs/tutorials/payments/send-confidential-payments)
