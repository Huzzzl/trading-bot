# S62 Shadow Scheduler Runbook (S63)

This runbook describes how to schedule the S62 forward-only shadow
experiment on Windows **without touching the existing paper trading
task**. The two tasks are completely independent: the shadow task
only *reads* what the paper task already wrote (its audit record and
the refreshed SPY 60m cache) and never submits, cancels, or reads
broker orders/positions.

## 1. Leave the existing paper task unchanged

Do not edit, disable, re-register, or rename the existing paper
trading Scheduled Task in any way as part of this runbook. It
continues to run exactly as it does today:

1. force-refresh the SPY 60m Yahoo cache;
2. abort trading when the refresh fails;
3. run the paper cycle;
4. write one paper audit JSONL record under `logs/paper_cycles/`.

## 2. Inspect its current trigger time

Open Windows Task Scheduler and find the existing paper task. Note
its exact trigger time(s) — you will anchor the new shadow task
several minutes *after* this, not at a fixed clock time picked in
advance. There is no single correct offset for every environment;
use whatever gap reliably follows the paper task's typical
completion time on your machine.

## 3. Create a separate shadow task using the PowerShell launcher

Use `scripts/run_s62_shadow_task.ps1` as the task's action. It is a
thin, single-shot wrapper around:

```powershell
python -m src.tools.run_scheduled_shadow_cycle --json
```

Example action (fill in your own paths — do not copy these
verbatim):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\path\to\trading-bot\scripts\run_s62_shadow_task.ps1" -RepoRoot "C:\path\to\trading-bot" -PythonExe "C:\path\to\venv\Scripts\python.exe"
```

Give the new task a distinct name, for example `TradingBot-S62-Shadow`,
so it can never be confused with — or accidentally overwrite — the
existing paper task.

In the task's **Settings** tab, set **"If the task is already
running, then the following rule applies"** to **"Do not start a new
instance"**. The gate has its own single-writer lock as a second line
of defense, but the Scheduled Task setting is the first: it prevents
Task Scheduler itself from ever launching an overlapping invocation
in the first place.

## 4. Schedule shadow approximately five minutes after each paper invocation

Set the shadow task's trigger to fire a few minutes after the
paper task's trigger from step 2 (five minutes is a reasonable
starting point, but base the exact offset on your own environment's
observed paper-task completion time, not on a number copied from
this document). This gives the paper task time to finish writing its
audit record and refreshed cache before the gate checks them.

## 5. Use the same working repository and Python environment

Point `-RepoRoot` and `-PythonExe` at the same repository checkout
and Python interpreter/virtualenv that the paper task uses, so both
tasks observe the same `logs/`, `data/cache/`, and dependency
versions.

Record the exact values you used for `-RepoRoot` and `-PythonExe` (for
example, in the Scheduled Task's own description field, or in this
runbook's local notes) — you will need them again if you ever have to
manually reproduce a run to investigate an incomplete invocation (see
"Investigating an orphaned STARTED record" below).

## 6. Initially create the shadow task disabled

When registering the new Scheduled Task, leave it **disabled**. Do
not enable it yet.

## 7. Manually run it once

While the task is disabled, do NOT right-click it in Task Scheduler
and choose **Run** — a disabled task should stay untouched in the
scheduler itself. Instead, test the launcher directly from a
PowerShell prompt, exactly as the disabled task would invoke it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_s62_shadow_task.ps1 `
    -RepoRoot "C:\path\to\trading-bot" `
    -PythonExe "C:\path\to\venv\Scripts\python.exe"
```

## 8. Inspect scheduler audit, S62 status, and report

After the manual run, check all of:

```powershell
Get-Content logs\shadow_scheduler\<today>.jsonl | Select-Object -Last 2
python -m src.tools.run_shadow_strategy_cycle --status
python -m src.tools.shadow_strategy_report
```

Confirm:

* the run wrote exactly two new scheduler-audit lines for this
  invocation: one with `"phase": "STARTED"` and one with
  `"phase": "TERMINAL"`, both sharing the same `invocation_id`. A
  `STARTED` record with no matching `TERMINAL` record means the
  invocation did not complete cleanly — see "Investigating an
  orphaned STARTED record" below before proceeding;
* the `TERMINAL` record's `result` is `RUN` (or an expected `SKIPPED`
  reason if the paper audit/cache weren't ready yet) and `exit_code`
  is `0`;
* `research_only: true` and `automatic_promotion_allowed: false` /
  `automatic_strategy_promotion_allowed: false` appear in every
  relevant output;
* the S62 status and report commands complete without error.

## 9. Enable it only after the manual scheduled invocation passes

Only flip the task to **Enabled** in Task Scheduler once step 8 has
been verified clean. Do not enable it as part of initial
registration.

## 10. How to disable the shadow task without touching the paper task

The shadow task is fully independent, so disabling it never affects
the paper task:

```powershell
Disable-ScheduledTask -TaskName "TradingBot-S62-Shadow"
```

or, in the Task Scheduler UI, right-click **TradingBot-S62-Shadow**
(or whatever distinct name you gave it) → **Disable**. The paper
task keeps running exactly as before.

## Investigating an orphaned STARTED record

Every gate invocation that reaches the point of possibly invoking S62
writes a write-ahead `"phase": "STARTED"` scheduler-audit record
*before* S62 runs, and a `"phase": "TERMINAL"` record with the same
`invocation_id` after S62 finishes (or fails). If the machine loses
power, the process is killed, or Task Scheduler terminates the task
mid-run, you can be left with a `STARTED` record that has no matching
`TERMINAL` record — this is a detectable, distinguishable state, not
a silent "nothing happened."

To check for one manually:

```powershell
Get-Content logs\shadow_scheduler\*.jsonl | ForEach-Object { $_ | ConvertFrom-Json } |
    Group-Object invocation_id |
    Where-Object { -not ($_.Group.phase -contains "TERMINAL") -and ($_.Group.phase -contains "STARTED") }
```

Or, from the same Python environment the launcher uses:

```powershell
python -c "from pathlib import Path; from src.tools.run_scheduled_shadow_cycle import find_incomplete_invocations; import json; print(json.dumps(find_incomplete_invocations(Path('logs/shadow_scheduler')), indent=2))"
```

If an orphaned `STARTED` record is found:

1. **Do not re-enable or re-run the shadow task yet.** Confirm the
   task is not still actually running (check Task Scheduler's
   "Last Run Result" and whether the process is still alive).
2. Compare the `STARTED` record's `paper_audit_record_sha256` and
   `cache_latest_bar_ts` against the current paper audit and cache —
   if they still match, it is safe to invoke the gate again (it will
   compute the same deterministic `invocation_id` and either succeed
   cleanly or once again fail closed).
3. Check `logs/shadow_strategy/state.json` (and compare against
   `shadow_commit_observed` on any earlier, complete `TERMINAL`
   record) to see whether S62 actually advanced during the
   interrupted invocation, or whether it never got that far.
4. Run `python -m src.tools.run_shadow_strategy_cycle --status` — S62's
   own manifest/state/event-log validation will fail closed if the
   interrupted invocation left S62's own state inconsistent.
5. Once you understand what happened, invoke the launcher manually
   (step 7 above) to produce a clean, complete `STARTED`/`TERMINAL`
   pair before re-enabling the Scheduled Task.

## Reference: what the gate does and does not do

* Reads the latest completed paper-cycle audit record and verifies
  its SPY 60m cache refresh succeeded and matches the currently
  cached data.
* Invokes the existing S62 shadow runner (`run_shadow_strategy_cycle`)
  only when that verification passes — using the S62 module's own
  APIs, never by duplicating its trading logic.
* Writes one independent scheduler-audit *invocation* per gate run
  under `logs/shadow_scheduler/`, represented as a write-ahead
  `STARTED` record (persisted and `fsync`'d before S62 is invoked)
  and a `TERMINAL` record with the same `invocation_id` (persisted
  after S62 finishes or fails) — never a single record claimed
  up front, so an interrupted invocation remains detectable instead
  of silently indistinguishable from "S62 was never invoked."
* Never calls `run_automated_paper_cycle` or
  `run_paper_trading_cycle`.
* Never refreshes the cache itself.
* Never reads broker credentials, positions, or orders.
* Never submits, cancels, or replaces an order.
* Never modifies the S62 frozen manifest or promotes a shadow
  candidate — `research_only: true` and
  `automatic_strategy_promotion_allowed: false` remain true
  everywhere, always.
* A shadow-side failure never changes the paper audit or paper
  cache — the gate only reads those files.
