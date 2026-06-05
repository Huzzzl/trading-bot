# Controlled Report Persistence Design

Design document for future controlled persistence of offline research reports
produced by the S8 pipeline orchestrator.

**S9 is docs-only. No persistence is implemented in this PR.**
**No source code is added or changed.**
**No test files are added or changed.**
**No output artifacts are written or committed.**
**No broker/API/credential/env/network access is added.**
**No order submission. No live/paper trading approval.**

---

## 1. Scope and Status

### What S9 does

S9 defines the design for a future controlled report persistence layer.
It documents principles, artifact structure, metadata schema, safety gates,
and validation plan so that S10 can implement a controlled local snapshot
command with clear expectations.

### What S9 does not do

- S9 does not implement file writing.
- S9 does not implement a CLI, runner, or command.
- S9 does not write, read, or commit any report file.
- S9 does not implement any persistence code.
- S9 does not approve paper or live trading.
- S9 does not add broker, API, credential, env, network, or order access.

---

## 2. Persistence Principles

All future persistence must adhere to these principles:

| Principle | Requirement |
|-----------|-------------|
| Fail-closed | Any error in path validation, safety check, or serialisation must abort without writing. |
| Explicit output directory | Caller must supply the output directory explicitly; no default paths. |
| Deterministic naming | All filenames are derived deterministically from candidate IDs, timestamps, and schema versions. No random suffixes. |
| No credentials | No credential files, env vars, API tokens, or secret stores are read or written. |
| No broker/API/network | No Alpaca, no HTTP requests, no sockets, no external services. |
| No live/paper approval | Writing a report does not constitute or imply live or paper trading approval. |
| No overwrite by default | Refuse to overwrite existing files unless an explicit `allow_overwrite=True` flag is set. |
| Safety flag gate | Never write if any safety flag (`broker_calls_made`, `credentials_read`, `network_calls_made`, `order_action_requested`, `live_trading_allowed`) is `True`. |
| No automatic promotion | A persisted report is never automatically promoted to paper or live trading input. |

---

## 3. Proposed Future Artifact Structure

When implemented, a single pipeline run produces a controlled local
directory structure under a caller-supplied `output_dir`. No files are
written to `data/`, `output/`, or any directory outside `output_dir`.

```
<output_dir>/
  <run_id>/
    manifest.json          # run metadata + file inventory
    pipeline_summary.json  # compact PipelineSummary fields
    reports/
      <candidate_id>.json  # one ResearchReport JSON per candidate
    summary.md             # optional human-readable markdown summary
```

### File naming

| File | Naming rule |
|------|-------------|
| `<run_id>/` | `{generated_at_utc_compact}_{result}` e.g. `20260604T000000Z_PASS` |
| `<candidate_id>.json` | `{candidate_id}.json`, derived from `CandidateSpec.candidate_id` |
| `manifest.json` | Fixed name within run directory |
| `pipeline_summary.json` | Fixed name within run directory |
| `summary.md` | Fixed name within run directory; only written if `include_markdown=True` |

---

## 4. Proposed Metadata Schema

### manifest.json

```json
{
  "schema_version": "S9/1.0",
  "generated_at_utc": "<ISO-8601>",
  "run_id": "<run_id>",
  "result": "PASS|BLOCKED|ERROR",
  "blocker": null,
  "git_commit_sha": "<40-char hex or null if unavailable>",
  "candidate_filter": {
    "groups": [],
    "symbols": [],
    "intervals": [],
    "strategy_families": [],
    "holding_horizons": [],
    "max_candidates": null
  },
  "pipeline_params": {
    "start_date": "<ISO-8601>",
    "end_date": "<ISO-8601>",
    "train_months": 12,
    "test_months": 3,
    "step_months": 3,
    "cache_dir": "<path or null>"
  },
  "counts": {
    "candidates_selected": 0,
    "reports_created": 0,
    "reports_passed": 0,
    "reports_blocked": 0,
    "reports_error": 0
  },
  "best_candidate_id": null,
  "best_average_monthly_return": null,
  "worst_max_drawdown": null,
  "total_trades_sum": null,
  "safety": {
    "broker_calls_made": false,
    "credentials_read": false,
    "network_calls_made": false,
    "order_action_requested": false,
    "live_trading_allowed": false
  },
  "files": {
    "pipeline_summary": "pipeline_summary.json",
    "reports": ["<candidate_id>.json", "..."]
  }
}
```

All safety flags expected `false`. If any is `true`, writing must be refused.

### pipeline_summary.json

Serialisation of `PipelineSummary` fields plus the candidate filter,
pipeline params, and manifest `run_id`. Schema version `"S9/1.0"`.

### reports/<candidate_id>.json

Serialisation of a single `ResearchReport` via `research_report_to_dict()`.
Schema version `"S6/1.0"` (inherited from S6 report schema).

---

## 5. Safety Gates for Future Implementation

The future persistence function must enforce all of the following before
writing any file:

1. **Output directory supplied**: raise if `output_dir` is `None` or empty.
2. **Path safety**: refuse absolute paths outside a repo-controlled base
   directory unless `allow_unsafe_path=True` is explicitly passed.
3. **Refuse overwrite**: refuse to write into an existing run directory
   unless `allow_overwrite=True` is explicitly passed.
4. **Safety flag gate**: refuse to write if any of `broker_calls_made`,
   `credentials_read`, `network_calls_made`, `order_action_requested`,
   `live_trading_allowed` is `True` in the snapshot result.
5. **No live trading**: refuse to write if `live_trading_allowed` is `True`
   (redundant with gate 4, but must be checked independently).
6. **Atomic write**: write all files to a temporary directory first, then
   rename to the final run directory. Never leave a partial run.
7. **No credential access**: no `os.environ`, no credential files, no
   secret store reads at any point in the persistence path.
8. **No network access**: no HTTP, no socket, no broker calls.
9. **No order submission**: writing a report must not trigger or imply any
   order action.

---

## 6. Validation Plan for Future Implementation (S10)

When S10 implements the persistence layer, the following tests must be added:

| Test area | Description |
|-----------|-------------|
| No writes by default | `run_offline_research_pipeline()` with no `output_dir` writes nothing. |
| Deterministic filenames | Same inputs produce identical filenames across runs. |
| Manifest consistency | `manifest.json` files list matches actual files written. |
| Refusal on unsafe path | Raises on absolute/unsafe path without `allow_unsafe_path=True`. |
| Refusal on overwrite | Raises if run directory already exists without `allow_overwrite=True`. |
| Safety flag refusal | Raises if any safety flag is `True`; no files written. |
| No forbidden imports | Source scan: no `requests`, `aiohttp`, `alpaca`, `os.environ`, `urllib`. |
| No credential access | Source scan: no credential, token, API key reads. |
| Atomic write | Partial failure leaves no files in the final run directory. |
| Schema version | `manifest.json` schema_version is `"S9/1.0"`. |

---

## 7. Relationship to S10

S10 may implement a controlled local snapshot persistence command only after
S9 (this design) is merged to `main`. The following constraints apply to S10:

- S10 still must not approve paper or live trading.
- S10 still must not add broker, API, credential, env, network, or order access.
- S10 must implement all safety gates described in section 5 above.
- S10 must add tests covering all areas in section 6 above.
- S10 must not change `src/backtest/`, `src/strategy/`, `src/risk/`,
  `src/runtime/`, `src/execution/`, `src/main.py`, or any live fail-closed gate.
- S10 persistence output must go under a caller-supplied local directory;
  nothing is written to `data/`, `output/`, or committed to git automatically.
- `data/cache/` remains gitignored; cache files are never committed.
- `output/` remains gitignored; output files are never committed by default.

---

## 8. What This Document Is Not

This document is not:
- An approval for automated live trading.
- An approval for automated paper trading.
- An approval for any individual trade.
- An instruction to implement persistence now.
- A specification for broker connectivity.
- A credential management design.

All position and trading decisions remain entirely manual until automation
is implemented, tested, reviewed, and explicitly approved in its own PR.
