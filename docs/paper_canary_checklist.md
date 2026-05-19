# Paper Canary Checklist SOP

Concise operator checklist for safe paper canary runs.
Run through every numbered step in order. Stop and investigate at any unexpected result.

---

## 0. Preconditions

Before touching any config or running any command:

- [ ] Local `main` is synced with origin: `git pull origin main`
- [ ] Full test suite passes: `python -m pytest -q`
- [ ] Config (`config/settings.paper.local.yaml`) is in **preview mode**:
  - `paper_preview_only: true` (buy flow)
  - `paper_close_preview_only: true` (close flow)
  - `paper_kill_switch_enabled: false`
- [ ] Alpaca credentials are **not** exported in your shell unless you are about to run a live submit or `--verify-ledger`
- [ ] No open SPY position or open order in the Alpaca paper dashboard (<https://app.alpaca.markets>)

---

## 1. Daily Read-Only Checks

Run these every day before any submit. All are offline (no credentials needed).

```bash
# Smoke check — config + artifacts
python -m src.tools.paper_smoke_check \
    --config config/settings.paper.local.yaml \
    --output-dir output/paper_smoke_check

# Full status + safety guards
python -m src.tools.paper_status \
    --config config/settings.paper.local.yaml \
    --output-dir output/paper_smoke_check \
    --ledger output/paper_execution_ledger.csv

# Pre-submit checklist (key gate before any submit)
python -m src.tools.paper_pre_submit_check \
    --config config/settings.paper.local.yaml \
    --output-dir output/paper_smoke_check \
    --ledger output/paper_execution_ledger.csv
```

PowerShell equivalents:

```powershell
python -m src.tools.paper_smoke_check `
    --config config/settings.paper.local.yaml `
    --output-dir output/paper_smoke_check

python -m src.tools.paper_status `
    --config config/settings.paper.local.yaml `
    --output-dir output/paper_smoke_check `
    --ledger output/paper_execution_ledger.csv

python -m src.tools.paper_pre_submit_check `
    --config config/settings.paper.local.yaml `
    --output-dir output/paper_smoke_check `
    --ledger output/paper_execution_ledger.csv
```

**Stop if any check exits 1 or shows FAIL.**

Optional — verify existing ledger rows against Alpaca (requires credentials):

```bash
# Export credentials first
export ALPACA_API_KEY="your-paper-key"
export ALPACA_SECRET_KEY="your-paper-secret"

python -m src.tools.paper_pre_submit_check \
    --config config/settings.paper.local.yaml \
    --output-dir output/paper_smoke_check \
    --ledger output/paper_execution_ledger.csv \
    --verify-ledger
```

```powershell
$env:ALPACA_API_KEY    = "your-paper-key"
$env:ALPACA_SECRET_KEY = "your-paper-secret"

python -m src.tools.paper_pre_submit_check `
    --config config/settings.paper.local.yaml `
    --output-dir output/paper_smoke_check `
    --ledger output/paper_execution_ledger.csv `
    --verify-ledger
```

---

## 2. Buy Canary Flow

### 2a. Run buy preview

Config must have `paper_preview_only: true`. No credentials needed.

```bash
python -m src.main
```

```powershell
python -m src.main
```

### 2b. Inspect candidates

Open `output/paper_candidate_intents.csv`. Review every row:

- [ ] `symbol` is `SPY`
- [ ] `side` is `buy`
- [ ] `order_type` is `market`
- [ ] `quantity` is `1.0` (or matches your `paper_order_quantity_override`)
- [ ] Choose one `client_order_id` — record it

### 2c. Confirm the chosen ID is unused

```bash
grep "BT-YYYYMMDDHHMMSS-SPY" output/paper_execution_ledger.csv
```

```powershell
Select-String -Path output/paper_execution_ledger.csv -Pattern "BT-YYYYMMDDHHMMSS-SPY"
```

Expected: **no match** (if the ID already appears, choose a different one).

### 2d. Switch config to buy submit

Edit `config/settings.paper.local.yaml`:

```yaml
execution:
  paper_preview_only: false
  paper_selected_client_order_id: "BT-YYYYMMDDHHMMSS-SPY"   # ← your chosen ID
```

### 2e. Run pre-submit checklist

```bash
python -m src.tools.paper_pre_submit_check \
    --config config/settings.paper.local.yaml \
    --output-dir output/paper_smoke_check \
    --ledger output/paper_execution_ledger.csv
```

```powershell
python -m src.tools.paper_pre_submit_check `
    --config config/settings.paper.local.yaml `
    --output-dir output/paper_smoke_check `
    --ledger output/paper_execution_ledger.csv
```

**Expected output includes:**
- `[WARN] mode_summary  (SUBMIT MODE DETECTED — review selected client_order_id=...)`
- `[PASS] submit_readiness` (kill switch off, market open, limits not reached)
- `RESULT: WARN` (WARN is normal in submit mode; FAIL means do not proceed)

**Stop if RESULT is FAIL.**

### 2f. Export credentials and submit

```bash
export ALPACA_API_KEY="your-paper-key"
export ALPACA_SECRET_KEY="your-paper-secret"
python -m src.main
```

```powershell
$env:ALPACA_API_KEY    = "your-paper-key"
$env:ALPACA_SECRET_KEY = "your-paper-secret"
python -m src.main
```

### 2g. Inspect output artifacts

- [ ] `output/order_results.csv` — confirm `status=accepted` (or `filled`)
- [ ] `output/paper_order_status_poll.json` — confirm final `status` is not `rejected`
- [ ] `output/order_reconciliation.json` — confirm `overall_status=PASS`
- [ ] `output/paper_execution_ledger.csv` — confirm new row appended with correct `client_order_id`

### 2h. Run ledger verification

```bash
python -m src.tools.paper_ledger_verify \
    --ledger output/paper_execution_ledger.csv \
    --output output/paper_ledger_verification.csv
```

```powershell
python -m src.tools.paper_ledger_verify `
    --ledger output/paper_execution_ledger.csv `
    --output output/paper_ledger_verification.csv
```

- [ ] Verify `status_match=True` for the new row in `paper_ledger_verification.csv`

---

## 3. Close Canary Flow

### 3a. Run close preview

Config must have `paper_close_positions_enabled: true` and `paper_close_preview_only: true`.

```bash
python -m src.main
```

```powershell
python -m src.main
```

### 3b. Inspect close candidates

Open `output/paper_close_candidate_intents.csv`:

- [ ] `symbol` is `SPY`
- [ ] `side` is `sell`
- [ ] `order_type` is `market`
- [ ] `quantity` matches current position
- [ ] `client_order_id` follows stable format `BC-YYYYMMDD-SPY-CLOSE`

Record the `client_order_id` (it is deterministic for the same day — no need to choose).

### 3c. Switch config to close submit

Edit `config/settings.paper.local.yaml`:

```yaml
execution:
  paper_close_positions_enabled: true
  paper_close_preview_only: false
  paper_selected_close_client_order_id: "BC-YYYYMMDD-SPY-CLOSE"   # ← today's date
```

### 3d. Run pre-submit checklist

```bash
python -m src.tools.paper_pre_submit_check \
    --config config/settings.paper.local.yaml \
    --output-dir output/paper_smoke_check \
    --ledger output/paper_execution_ledger.csv
```

```powershell
python -m src.tools.paper_pre_submit_check `
    --config config/settings.paper.local.yaml `
    --output-dir output/paper_smoke_check `
    --ledger output/paper_execution_ledger.csv
```

**Stop if RESULT is FAIL.**

### 3e. Submit close

```bash
python -m src.main
```

```powershell
python -m src.main
```

### 3f. Inspect output artifacts

- [ ] `output/order_results.csv` — confirm sell accepted
- [ ] `output/paper_order_status_poll.json` — confirm final status
- [ ] `output/order_reconciliation.json` — confirm `overall_status=PASS`
- [ ] `output/paper_execution_ledger.csv` — confirm new `close_submit` row

### 3g. Run ledger verification

```bash
python -m src.tools.paper_ledger_verify \
    --ledger output/paper_execution_ledger.csv \
    --output output/paper_ledger_verification.csv
```

```powershell
python -m src.tools.paper_ledger_verify `
    --ledger output/paper_execution_ledger.csv `
    --output output/paper_ledger_verification.csv
```

---

## 4. Post-Run Cleanup

After every canary run (buy or close):

1. **Reset config to preview mode:**

```yaml
execution:
  paper_preview_only: true
  paper_close_preview_only: true
  paper_selected_client_order_id: null
  paper_selected_close_client_order_id: null
```

2. **Clear Alpaca credentials from your shell:**

```bash
unset ALPACA_API_KEY
unset ALPACA_SECRET_KEY
```

```powershell
Remove-Item Env:\ALPACA_API_KEY    -ErrorAction SilentlyContinue
Remove-Item Env:\ALPACA_SECRET_KEY -ErrorAction SilentlyContinue
```

3. **Back up ledger and verification CSV:**

```bash
cp output/paper_execution_ledger.csv output/paper_execution_ledger_$(date +%Y%m%d).csv
cp output/paper_ledger_verification.csv output/paper_ledger_verification_$(date +%Y%m%d).csv
```

```powershell
$d = Get-Date -Format yyyyMMdd
Copy-Item output/paper_execution_ledger.csv     "output/paper_execution_ledger_$d.csv"
Copy-Item output/paper_ledger_verification.csv  "output/paper_ledger_verification_$d.csv"
```

---

## 5. Emergency Stop

If anything goes wrong at any step:

1. **Ctrl+C** the running process immediately.
2. Enable the kill switch in `config/settings.paper.local.yaml`:

```yaml
execution:
  paper_kill_switch_enabled: true
```

3. Open <https://app.alpaca.markets> → **Orders** → cancel any open paper orders manually.
4. Investigate before re-enabling (`paper_kill_switch_enabled: false`).

---

## 6. Daily Limit Reminder

Before submitting, always check `paper_pre_submit_check` output:

```
[PASS] daily_usage  (today: total=2 buy=2 close=1)
       remaining  total=1  buy=0  close=1
```

**If `remaining_buy_orders=0` or `remaining_total_orders=0` → do not submit that day.**

The daily caps are configured in `settings.yaml`:

```yaml
execution:
  paper_daily_max_orders: 3
  paper_daily_max_buy_orders: 2
  paper_daily_max_close_orders: 2
```

---

## Quick Reference

| Task | Command |
|------|---------|
| Smoke check | `python -m src.tools.paper_smoke_check --config ... --output-dir ...` |
| Full status | `python -m src.tools.paper_status --config ... --output-dir ... --ledger ...` |
| Pre-submit gate | `python -m src.tools.paper_pre_submit_check --config ... --output-dir ... --ledger ...` |
| Buy preview | `python -m src.main` (preview_only: true) |
| Buy submit | `python -m src.main` (preview_only: false, selected_id set) |
| Close preview | `python -m src.main` (close_positions: true, close_preview_only: true) |
| Close submit | `python -m src.main` (close_positions: true, close_preview_only: false) |
| Ledger verify | `python -m src.tools.paper_ledger_verify --ledger ... --output ...` |
| Kill switch ON | `paper_kill_switch_enabled: true` in config |

---

*See `docs/paper_execution_runbook.md` for detailed field references and guard descriptions.*
