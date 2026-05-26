# Post-Submit Manual Position Handling Runbook

Steps the operator should follow after a real live submit has returned
`result="SUBMITTED"`.

**This PR does NOT trade.**
**This PR does NOT submit, sell, cancel, or replace any order.**
**This PR does NOT contact Alpaca.**
**This PR does NOT read credentials.**
**This PR does NOT approve future trading.**
**Existing code only supports the prior single manual buy attempt.**
**Automated position management is not implemented.**

---

## 1. Immediate Checks After SUBMITTED

Perform these checks immediately after observing `result="SUBMITTED"`.

### Credentials

- [ ] `ALPACA_LIVE_API_KEY` has been unset from the terminal session
- [ ] `ALPACA_LIVE_SECRET_KEY` has been unset from the terminal session
- [ ] Neither credential is stored in any file, shell profile, or log

Verify:

```sh
echo "${ALPACA_LIVE_API_KEY:-<unset>}"
echo "${ALPACA_LIVE_SECRET_KEY:-<unset>}"
```

Both should print `<unset>`.

### Local operator config reset

- [ ] `live_trading_enabled` reset to `false`
- [ ] `live_submit_dry_run` reset to `true`
- [ ] `live_kill_switch_enabled` reset to `true`

Local operator config after reset:

```yaml
live_trading_enabled: false
live_submit_dry_run: true
live_kill_switch_enabled: true
```

### Output artifacts and ledger

- [ ] Output artifact (`live_single_manual_submit_attempt.json`) is **not**
  committed — confirm with `git status`
- [ ] Ledger CSV is **not** committed — confirm with `git status`
- [ ] `settings.yaml` is unchanged — confirm with `git diff`

### Alpaca UI — order status

- [ ] Log in to the Alpaca dashboard (live account, not paper)
- [ ] Locate the SPY market buy order submitted in this attempt
- [ ] Confirm the order status (pending, filled, partially filled, rejected,
  cancelled, expired)
- [ ] Note whether a SPY position now exists in the account

---

## 2. Manual Position Decision

After confirming order and fill status in the Alpaca UI, the operator must
make a manual decision about the resulting position (if any).

**The bot does not make this decision automatically.**
**No code in this repository implements position management.**

### Option A — Close/sell the position manually

If the operator only intended to validate the live-submit pipeline, or
otherwise does not wish to hold the position, they may sell it manually
through the Alpaca UI.

See Section 3 for the manual sell process.

### Option B — Keep the position

If the operator chooses to hold the SPY position, that is a manual
investment decision outside the scope of this bot.

The bot will not monitor, manage, hedge, or close the position automatically.
No stop-loss, take-profit, or time-based exit logic exists in this codebase.

### If the order did not fill

If the order was rejected, expired, or cancelled by the broker without
filling, no position exists and no sell action is required.  Check the
Alpaca UI for the exact reason and make a note for records.

---

## 3. Manual Sell / Close Process

**Use the Alpaca UI only.  Do not use code for this.**

### Steps

1. Log in to the Alpaca dashboard (live account).
2. Navigate to the Positions page.
3. Locate the SPY position.
4. Use the Alpaca UI's sell or close-position action.
5. Confirm the order type and quantity before submitting.
6. Monitor the resulting sell order to completion.

### Constraints

- [ ] Do not use `live_single_manual_submit` to issue the sell — it only
  supports `side=buy` and will BLOCKED on any other side
- [ ] Do not reuse the existing `live_single_submit_approval_review` artifact
  for a sell — it is scoped to a single buy only
  (`approval_scope="AUTHORIZE_SINGLE_LIVE_MARKET_BUY_SPY_ONCE"`)
- [ ] Do not retry if anything is unclear — pause and investigate first
- [ ] Do not add a sell code path in this PR
- [ ] Do not implement `cancel_order` or `replace_order` in this PR

---

## 4. What Not To Do

The following actions must **not** be taken after the first live buy.

| Action | Why not |
|--------|---------|
| Run `live_single_manual_submit` again without fresh approvals | The prior approval artifact is consumed — a new approval is required for any future attempt |
| Pass `--allow-real-live-submit-once` again without fresh prerequisite artifacts | All four prerequisite artifacts must be re-run with current results |
| Automate a sell or position close | Automated selling is not implemented and not approved |
| Implement cancel/replace/sell logic in this PR | Out of scope — requires separate design, approval, and test coverage |
| Commit output artifacts, ledger, credentials, order IDs, fill details, account details, balances, or broker response details | These must remain local only and never appear in the repository |

---

## 5. Future Engineering Options

The following items may be designed and implemented in future PRs.  None
are approved or implemented by this PR.

- **Manual sell approval flow** — A separate `live_single_manual_sell`
  tool analogous to `live_single_manual_submit`, with its own approval
  artifact, preflight, and explicit CLI flag.  All tests mock-only before
  any real sell adapter is implemented.

- **Read-only position reconciliation tool** — A read-only tool that reads
  current Alpaca positions and open orders without submitting anything,
  for operator awareness only.

- **Position status snapshot doc** — A non-sensitive snapshot document
  recording the observed position state after the first buy, analogous
  to existing preflight and submit snapshots.

- **Mock-only test coverage before any real sell adapter** — All tests for
  any future sell tool must be mock-only by default, with the same
  structural constraints as `tests/test_live_single_manual_submit.py`.

None of these items are implemented, scheduled, or approved by this PR.

---

## 6. Warning

> **The first live buy success does not approve future trading.**
> **This runbook does not approve future trading.**
> **No sell, cancel, or replace order is issued by this PR.**
> **No code changes are made by this PR.**
>
> Any future live buy or sell attempt requires:
> - Fresh `result="PASS"` runs of all prerequisite tools
> - A fresh, unexpired operator approval artifact scoped to that specific
>   action
> - A fresh live broker read-only preflight from the same trading session
> - Explicit local operator config overrides (never committed)
> - Valid credentials in environment variables
> - An explicit CLI flag for that specific action
>
> Emergency actions (cancel, close, replace) remain manual via the Alpaca
> broker UI only — the code has no `cancel_order` or `replace_order` logic.
>
> The operator is solely responsible for all manual trading decisions,
> including whether to hold or close the position resulting from the first
> live buy.

---

## Suggested Git Tag

```
post-submit-manual-position-handling-runbook-prepared
```

---

## References

- `docs/first_real_live_submit_success_snapshot.md` — first live submit success snapshot (PR #125)
- `docs/real_submit_final_operator_runbook.md` — real submit operator runbook (PR #124)
- `docs/real_submit_without_flag_blocked_snapshot.md` — BLOCKED dry-run snapshot (PR #123)
- `docs/live_readiness_status.md` — full readiness status and milestone history
- `src/tools/live_single_manual_submit.py` — real adapter implementation (PR #122)
