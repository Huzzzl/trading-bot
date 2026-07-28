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

## 6. Initially create the shadow task disabled

When registering the new Scheduled Task, leave it **disabled**. Do
not enable it yet.

## 7. Manually run it once

Right-click the disabled task in Task Scheduler and choose **Run**,
or invoke the launcher directly from a PowerShell prompt:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_s62_shadow_task.ps1 `
    -RepoRoot "C:\path\to\trading-bot" `
    -PythonExe "C:\path\to\venv\Scripts\python.exe"
```

## 8. Inspect scheduler audit, S62 status, and report

After the manual run, check all three of:

```powershell
Get-Content logs\shadow_scheduler\<today>.jsonl | Select-Object -Last 1
python -m src.tools.run_shadow_strategy_cycle --status
python -m src.tools.shadow_strategy_report
```

Confirm:

* the scheduler audit record's `result` is `RUN` (or an expected
  `SKIPPED` reason if the paper audit/cache weren't ready yet) and
  `exit_code` is `0`;
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

## Reference: what the gate does and does not do

* Reads the latest completed paper-cycle audit record and verifies
  its SPY 60m cache refresh succeeded and matches the currently
  cached data.
* Invokes the existing S62 shadow runner (`run_shadow_strategy_cycle`)
  only when that verification passes — using the S62 module's own
  APIs, never by duplicating its trading logic.
* Writes one independent scheduler audit record per invocation under
  `logs/shadow_scheduler/`.
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
