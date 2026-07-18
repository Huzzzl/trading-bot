"""
tools/run_shadow_strategy_cycle.py
==================================
S62 forward-only shadow strategy validation.

This tool collects **new** forward observations for five predeclared
candidates. It is deliberately isolated from the paper execution
path — nothing here submits, cancels, replaces, or queries broker
orders, and the production paper strategy (SPY 60m SMA 20/100 with
``max_position_fraction=0.01``) is untouched.

Safety boundaries
-----------------
* No broker imports.
* No credentials read.
* No modification of paper strategy, risk limits, or scheduler.
* All state and events live under ``logs/shadow_strategy/`` (gitignored).
* Runner exit code 0 = success, non-zero = failure (fail closed).

Frozen manifest
---------------
* ``experiment_id: "S62_SPY_60M_FORWARD"``
* ``forward_cutoff_utc: "2026-07-17T19:30:00Z"``
* candidates (id / short / long / filter):
  - paper_control_20_100_none                  20 / 100 / none
  - research_10_20_none                        10 /  20 / none
  - research_10_20_separation25                10 /  20 / ma_separation_25bps
  - research_10_20_trend200_separation25       10 /  20 / trend200_and_separation25
  - research_15_50_none                        15 /  50 / none

Modes
-----
    --once      process any new bars (default action)
    --status    print current state, do not process
    --dry-run   process in memory but do not persist
    --json      emit machine-readable summary

Bars at or before the cutoff are indicator warmup only. Bars strictly
after the cutoff drive shadow trading and diagnostic counters. All
processing is idempotent — duplicate bars, reruns, and out-of-order
inputs never double-count.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from src.tools.backtest_strategy_eval import (
    Bar, load_cached_bars, _sma, _filter_allow, _FILTER_VARIANTS,
)

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

EXPERIMENT_ID = "S62_SPY_60M_FORWARD"
FORWARD_CUTOFF_UTC = "2026-07-17T19:30:00Z"
_DEFAULT_STATE_DIR = Path("logs/shadow_strategy")
_MANIFEST_FILENAME = "manifest.json"
_STATE_FILENAME = "state.json"
_EVENTS_FILENAME = "events.jsonl"
_LOCK_FILENAME = ".lock"
_INITIAL_EQUITY = 10_000.0
_SLIPPAGE_BPS = 1
_COMMISSION_BPS = 0
_DEFAULT_CACHE_DIR = Path("data/cache")

_CANDIDATE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {"id": "paper_control_20_100_none",
     "short_window": 20, "long_window": 100, "filter": "none"},
    {"id": "research_10_20_none",
     "short_window": 10, "long_window": 20, "filter": "none"},
    {"id": "research_10_20_separation25",
     "short_window": 10, "long_window": 20, "filter": "ma_separation_25bps"},
    {"id": "research_10_20_trend200_separation25",
     "short_window": 10, "long_window": 20, "filter": "trend200_and_separation25"},
    {"id": "research_15_50_none",
     "short_window": 15, "long_window": 50, "filter": "none"},
)


class ShadowError(Exception):
    """Any shadow runner failure — always fatal."""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(now_utc: datetime) -> dict[str, Any]:
    """Return the frozen manifest with a deterministic content hash.

    ``created_at_utc`` and ``candidate_manifest_sha256`` are excluded
    from the hashed body so the hash depends only on the frozen
    definitions (candidates + cutoff + execution assumptions).
    """
    m: dict[str, Any] = {
        "experiment_id": EXPERIMENT_ID,
        "created_at_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "symbol": "SPY",
        "interval": "60m",
        "execution": "next_open",
        "commission_bps": _COMMISSION_BPS,
        "slippage_bps": _SLIPPAGE_BPS,
        "forward_cutoff_utc": FORWARD_CUTOFF_UTC,
        "candidates": [dict(c) for c in _CANDIDATE_DEFINITIONS],
        "research_only": True,
        "automatic_strategy_promotion_allowed": False,
    }
    m["candidate_manifest_sha256"] = manifest_hash(m)
    return m


def manifest_hash(m: dict[str, Any]) -> str:
    core = {k: v for k, v in m.items()
            if k not in ("created_at_utc", "candidate_manifest_sha256")}
    canonical = json.dumps(core, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_or_init_manifest(state_dir: Path, now_utc: datetime) -> dict[str, Any]:
    """Load an existing manifest, verifying it matches the frozen set,
    or write a new one on first run."""
    path = state_dir / _MANIFEST_FILENAME
    canonical = build_manifest(now_utc)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ShadowError(f"manifest file is malformed: {exc}") from exc
        stored_hash = loaded.get("candidate_manifest_sha256")
        recomputed = manifest_hash(loaded)
        if stored_hash != recomputed:
            raise ShadowError(
                f"stored manifest hash {stored_hash!r} does not match "
                f"computed {recomputed!r} — the manifest file has been altered"
            )
        if stored_hash != canonical["candidate_manifest_sha256"]:
            raise ShadowError(
                "manifest has drifted from the frozen S62 definitions — "
                "candidates, cutoff, execution, commission, or slippage "
                "cannot change once events exist"
            )
        return loaded
    state_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, canonical)
    return canonical


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _new_candidate_state(candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "cash": _INITIAL_EQUITY,
        "quantity": 0.0,
        "position_open": False,
        "entry_price": None,
        "entry_timestamp_utc": None,
        "realized_equity": _INITIAL_EQUITY,
        "marked_equity": _INITIAL_EQUITY,
        "peak_equity": _INITIAL_EQUITY,
        "max_drawdown": 0.0,
        "processed_through_utc": None,
        "pending_action": None,
        "completed_trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "cumulative_exposure_bars": 0,
        "processed_forward_bar_count": 0,
        # Precise entry-signal counters.
        "bullish_crossover_count": 0,
        "bearish_crossover_count": 0,
        "bullish_state_bar_count": 0,
        "bullish_signal_count": 0,  # alias of bullish_state_bar_count
        "unique_bullish_episode_count": 0,
        "filter_evaluation_count": 0,
        "filter_allowed_count": 0,
        "filter_blocked_count": 0,
        "blocked_on_crossover_count": 0,
        "immediate_entry_count": 0,
        "delayed_entry_count": 0,
        "episodes_without_entry_count": 0,
        # Internal episode tracking.
        "_in_bullish_episode": False,
        "_episode_had_entry": False,
        "_first_forward_bar_utc": None,
        # Trade + return log for report metrics.
        "trades": [],
        "returns_series": [],
    }


def _init_state(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiment_id": manifest["experiment_id"],
        "manifest_hash": manifest["candidate_manifest_sha256"],
        "candidates": {
            c["id"]: _new_candidate_state(c["id"])
            for c in manifest["candidates"]
        },
    }


def load_or_init_state(state_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    path = state_dir / _STATE_FILENAME
    if not path.exists():
        return _init_state(manifest)
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShadowError(f"state file is malformed: {exc}") from exc
    if s.get("experiment_id") != manifest["experiment_id"]:
        raise ShadowError(
            f"state.json experiment_id {s.get('experiment_id')!r} does not "
            f"match manifest {manifest['experiment_id']!r}"
        )
    if s.get("manifest_hash") != manifest["candidate_manifest_sha256"]:
        raise ShadowError(
            "state.json manifest_hash does not match current manifest — "
            "candidates or cutoff have drifted"
        )
    # Ensure every candidate has a state block (adding a candidate is
    # part of the frozen definition change, so this is defensive only).
    s.setdefault("candidates", {})
    for c in manifest["candidates"]:
        if c["id"] not in s["candidates"]:
            s["candidates"][c["id"]] = _new_candidate_state(c["id"])
    return s


def _atomic_write_json(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    tmp.write_text(data, encoding="utf-8")
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _event_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _make_event(
    candidate_id: str,
    event_type: str,
    signal_bar_utc: str,
    *,
    execution_bar_utc: str | None = None,
    manifest_hash_str: str,
    short_sma: float | None = None,
    long_sma: float | None = None,
    filter_name: str | None = None,
    filter_result: bool | None = None,
    price: float | None = None,
    quantity: float | None = None,
    cash: float | None = None,
    equity: float | None = None,
    position_state: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": _event_id(
            EXPERIMENT_ID, candidate_id, event_type,
            signal_bar_utc, execution_bar_utc or "",
        ),
        "experiment_id": EXPERIMENT_ID,
        "candidate_id": candidate_id,
        "event_type": event_type,
        "signal_bar_utc": signal_bar_utc,
        "execution_bar_utc": execution_bar_utc,
        "event_timestamp_utc": execution_bar_utc or signal_bar_utc,
        "reason": reason,
        "short_sma": short_sma,
        "long_sma": long_sma,
        "filter": filter_name,
        "filter_result": filter_result,
        "price": price,
        "quantity": quantity,
        "cash": cash,
        "equity": equity,
        "position_state": position_state,
        "manifest_hash": manifest_hash_str,
    }


def _load_known_event_ids(events_path: Path) -> set[str]:
    known: set[str] = set()
    if not events_path.exists():
        return known
    with events_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if "event_id" in e:
                    known.add(e["event_id"])
            except json.JSONDecodeError:
                continue
    return known


def _append_events(events_path: Path, events: Sequence[dict[str, Any]]) -> None:
    if not events:
        return
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, sort_keys=True, default=str) + "\n")


# ---------------------------------------------------------------------------
# File locking (single-writer enforcement)
# ---------------------------------------------------------------------------


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "_FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(
                str(self.path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise ShadowError(
                f"another shadow runner is holding {self.path} — "
                "concurrent writers are not allowed"
            ) from exc
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                try:
                    self.path.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _parse_ts(s: str) -> datetime:
    ts = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bar_ts_utc(bar: Bar) -> datetime:
    ts = bar.ts
    if isinstance(ts, datetime):
        return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc,
        )
    if isinstance(ts, str):
        return _parse_ts(ts)
    to_pydt = getattr(ts, "to_pydatetime", None)
    if callable(to_pydt):
        dt = to_pydt()
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc,
        )
    raise ShadowError(f"unknown bar timestamp type: {type(ts).__name__}")


# ---------------------------------------------------------------------------
# Bar processing
# ---------------------------------------------------------------------------


def _apply_slippage(price: float, side: str) -> float:
    """Match the S56+ backtest cost convention: buys pay the ask, sells
    hit the bid — scale price by ``(1 ± slippage_bps/10_000)``."""
    slip = _SLIPPAGE_BPS / 10_000.0
    return price * (1 + slip) if side == "buy" else price * (1 - slip)


def _process_candidate(
    candidate_def: dict[str, Any],
    candidate_state: dict[str, Any],
    bars: Sequence[Bar],
    cutoff: datetime,
    manifest_hash_str: str,
    events_out: list[dict[str, Any]],
) -> None:
    """Advance one candidate through every unseen bar.

    Bar semantics:
    * ``bar[i]`` has open/high/low/close.
    * The SMA / filter decision for a fill at ``bar[i].open`` uses
      only ``closes[0..i-1]`` (guarded by ``prev_short``/``prev_long``
      below — the ``short_sma`` computed at index ``i`` is only used
      to *schedule* the action for ``bar[i+1].open``).
    """
    short_w = int(candidate_def["short_window"])
    long_w = int(candidate_def["long_window"])
    variant = candidate_def["filter"]
    cid = candidate_def["id"]

    processed_through = candidate_state.get("processed_through_utc")
    processed_dt = _parse_ts(processed_through) if processed_through else None

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    for i, bar in enumerate(bars):
        bar_ts = _bar_ts_utc(bar)

        # Skip already-processed bars — deterministic idempotency.
        if processed_dt is not None and bar_ts <= processed_dt:
            continue

        is_forward = bar_ts > cutoff

        # Step 1: execute any pending action at bar i's open.
        pa = candidate_state.get("pending_action")
        if is_forward and pa is not None:
            _execute_pending(candidate_state, pa, bar, bar_ts, variant,
                             manifest_hash_str, cid, events_out)

        # Step 2: compute SMAs and detect crossovers using data
        # completed through bar i (used to schedule bar i+1).
        short_sma = _sma(closes, short_w, i)
        long_sma = _sma(closes, long_w, i)
        prev_short = _sma(closes, short_w, i - 1) if i > 0 else None
        prev_long = _sma(closes, long_w, i - 1) if i > 0 else None

        if is_forward and short_sma is not None and long_sma is not None:
            bullish_cross = False
            bearish_cross = False
            if prev_short is not None and prev_long is not None:
                bullish_cross = (
                    prev_short <= prev_long and short_sma > long_sma
                )
                bearish_cross = (
                    prev_short >= prev_long and short_sma < long_sma
                )

            if bullish_cross:
                candidate_state["bullish_crossover_count"] += 1
                candidate_state["_in_bullish_episode"] = True
                candidate_state["_episode_had_entry"] = False
                events_out.append(_make_event(
                    cid, "BULLISH_CROSSOVER", bar_ts.isoformat(),
                    manifest_hash_str=manifest_hash_str,
                    short_sma=short_sma, long_sma=long_sma,
                    filter_name=variant,
                ))

            if bearish_cross:
                candidate_state["bearish_crossover_count"] += 1
                if candidate_state.get("_in_bullish_episode"):
                    candidate_state["unique_bullish_episode_count"] += 1
                    if not candidate_state.get("_episode_had_entry"):
                        candidate_state["episodes_without_entry_count"] += 1
                candidate_state["_in_bullish_episode"] = False
                candidate_state["_episode_had_entry"] = False
                events_out.append(_make_event(
                    cid, "BEARISH_CROSSOVER", bar_ts.isoformat(),
                    manifest_hash_str=manifest_hash_str,
                    short_sma=short_sma, long_sma=long_sma,
                    filter_name=variant,
                ))
                # Bearish exits bypass the entry filter — always
                # schedule the SELL when position is open.
                if candidate_state["position_open"]:
                    candidate_state["pending_action"] = {
                        "side": "sell",
                        "signal_bar_utc": bar_ts.isoformat(),
                        "reason": "bearish_crossover",
                    }
                    events_out.append(_make_event(
                        cid, "SHADOW_SELL_SCHEDULED", bar_ts.isoformat(),
                        manifest_hash_str=manifest_hash_str,
                        short_sma=short_sma, long_sma=long_sma,
                        filter_name=variant,
                        reason="bearish_crossover",
                    ))

            # Path-dependent bullish state bar — flat and bullish SMA.
            is_flat = candidate_state["quantity"] == 0.0
            bullish_state = short_sma > long_sma
            if is_flat and bullish_state:
                candidate_state["bullish_state_bar_count"] += 1
                candidate_state["bullish_signal_count"] = (
                    candidate_state["bullish_state_bar_count"]
                )
                allowed = _filter_allow(
                    variant, signal_index=i,
                    closes=closes, highs=highs, lows=lows,
                    short_window=short_w, long_window=long_w,
                )
                candidate_state["filter_evaluation_count"] += 1
                if allowed:
                    candidate_state["filter_allowed_count"] += 1
                    entry_type = "immediate" if bullish_cross else "delayed"
                    candidate_state["pending_action"] = {
                        "side": "buy",
                        "signal_bar_utc": bar_ts.isoformat(),
                        "reason": "bullish_crossover" if bullish_cross
                                  else "bullish_state_after_block",
                        "entry_type": entry_type,
                    }
                    events_out.append(_make_event(
                        cid, "SHADOW_BUY_SCHEDULED", bar_ts.isoformat(),
                        manifest_hash_str=manifest_hash_str,
                        short_sma=short_sma, long_sma=long_sma,
                        filter_name=variant, filter_result=True,
                        reason=entry_type,
                    ))
                else:
                    candidate_state["filter_blocked_count"] += 1
                    if bullish_cross:
                        candidate_state["blocked_on_crossover_count"] += 1
                    events_out.append(_make_event(
                        cid, "ENTRY_FILTER_BLOCKED", bar_ts.isoformat(),
                        manifest_hash_str=manifest_hash_str,
                        short_sma=short_sma, long_sma=long_sma,
                        filter_name=variant, filter_result=False,
                        reason="filter_block",
                    ))

        if is_forward:
            candidate_state["processed_forward_bar_count"] += 1
            if candidate_state["_first_forward_bar_utc"] is None:
                candidate_state["_first_forward_bar_utc"] = bar_ts.isoformat()
            if candidate_state["quantity"] > 0:
                candidate_state["cumulative_exposure_bars"] += 1

            prev_marked = candidate_state["marked_equity"]
            marked = (
                candidate_state["cash"]
                + candidate_state["quantity"] * bar.close
            )
            candidate_state["marked_equity"] = marked
            if marked > candidate_state["peak_equity"]:
                candidate_state["peak_equity"] = marked
            if candidate_state["peak_equity"] > 0:
                dd = (marked - candidate_state["peak_equity"]) / candidate_state["peak_equity"]
                if dd < candidate_state["max_drawdown"]:
                    candidate_state["max_drawdown"] = dd
            if prev_marked > 0:
                r = (marked - prev_marked) / prev_marked
                candidate_state["returns_series"].append(
                    {"ts": bar_ts.isoformat(), "r": r}
                )

            events_out.append(_make_event(
                cid, "BAR_PROCESSED", bar_ts.isoformat(),
                manifest_hash_str=manifest_hash_str,
                short_sma=short_sma, long_sma=long_sma,
                filter_name=variant,
                price=bar.close, equity=marked,
                position_state="long" if candidate_state["position_open"] else "flat",
            ))

        candidate_state["processed_through_utc"] = bar_ts.isoformat()
        processed_dt = bar_ts


def _execute_pending(
    candidate_state: dict[str, Any],
    pa: dict[str, Any],
    bar: Bar,
    bar_ts: datetime,
    variant: str,
    manifest_hash_str: str,
    cid: str,
    events_out: list[dict[str, Any]],
) -> None:
    side = pa["side"]
    if side == "buy":
        fill = _apply_slippage(bar.open, "buy")
        if fill > 0 and candidate_state["cash"] > 0:
            qty = candidate_state["cash"] / fill
            candidate_state["quantity"] = qty
            candidate_state["cash"] = 0.0
            candidate_state["position_open"] = True
            candidate_state["entry_price"] = fill
            candidate_state["entry_timestamp_utc"] = bar_ts.isoformat()
            candidate_state["_episode_had_entry"] = True
            entry_type = pa.get("entry_type")
            if entry_type == "immediate":
                candidate_state["immediate_entry_count"] += 1
            else:
                candidate_state["delayed_entry_count"] += 1
            events_out.append(_make_event(
                cid, "SHADOW_BUY_EXECUTED", pa["signal_bar_utc"],
                execution_bar_utc=bar_ts.isoformat(),
                manifest_hash_str=manifest_hash_str,
                filter_name=variant,
                price=fill, quantity=qty,
                cash=candidate_state["cash"],
                equity=qty * bar.open,
                position_state="long",
                reason=entry_type,
            ))
    elif side == "sell":
        fill = _apply_slippage(bar.open, "sell")
        sold_qty = candidate_state["quantity"]
        proceeds = sold_qty * fill
        entry_price = candidate_state["entry_price"] or fill
        pnl = (fill - entry_price) * sold_qty
        candidate_state["cash"] = proceeds
        candidate_state["quantity"] = 0.0
        candidate_state["position_open"] = False
        candidate_state["completed_trade_count"] += 1
        if pnl > 0:
            candidate_state["win_count"] += 1
        else:
            candidate_state["loss_count"] += 1
        candidate_state["trades"].append({
            "entry_ts": candidate_state["entry_timestamp_utc"],
            "exit_ts": bar_ts.isoformat(),
            "entry_price": entry_price,
            "exit_price": fill,
            "qty": sold_qty,
            "pnl": pnl,
        })
        candidate_state["realized_equity"] = proceeds
        candidate_state["entry_price"] = None
        candidate_state["entry_timestamp_utc"] = None
        events_out.append(_make_event(
            cid, "SHADOW_SELL_EXECUTED", pa["signal_bar_utc"],
            execution_bar_utc=bar_ts.isoformat(),
            manifest_hash_str=manifest_hash_str,
            filter_name=variant,
            price=fill, quantity=sold_qty,
            cash=proceeds, equity=proceeds,
            position_state="flat",
            reason=pa.get("reason", "bearish_crossover"),
        ))
    candidate_state["pending_action"] = None


# ---------------------------------------------------------------------------
# Top-level cycle
# ---------------------------------------------------------------------------


def _validate_bars(bars: Sequence[Bar]) -> list[Bar]:
    """Sort by timestamp and reject duplicates deterministically."""
    if not bars:
        return []
    with_ts = [(_bar_ts_utc(b), b) for b in bars]
    with_ts.sort(key=lambda t: t[0])
    seen: set[datetime] = set()
    out: list[Bar] = []
    for ts, b in with_ts:
        if ts in seen:
            continue
        seen.add(ts)
        out.append(b)
    return out


def run_cycle(
    bars: Sequence[Bar],
    *,
    state_dir: Path,
    now_utc: datetime,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process all unseen bars for all five candidates.

    Returns a JSON-serialisable summary describing what was processed.
    """
    manifest = load_or_init_manifest(state_dir, now_utc)
    cutoff = _parse_ts(manifest["forward_cutoff_utc"])
    validated = _validate_bars(bars)

    lock_path = state_dir / _LOCK_FILENAME
    with _FileLock(lock_path):
        state = load_or_init_state(state_dir, manifest)
        events_path = state_dir / _EVENTS_FILENAME
        known_event_ids = _load_known_event_ids(events_path)

        collected: list[dict[str, Any]] = []
        for cdef in manifest["candidates"]:
            cid = cdef["id"]
            cstate = state["candidates"][cid]
            per_candidate: list[dict[str, Any]] = []
            _process_candidate(
                cdef, cstate, validated, cutoff,
                manifest["candidate_manifest_sha256"], per_candidate,
            )
            # Idempotent event append.
            fresh = [
                e for e in per_candidate
                if e["event_id"] not in known_event_ids
            ]
            for e in fresh:
                known_event_ids.add(e["event_id"])
            collected.extend(fresh)

        if not dry_run:
            _append_events(events_path, collected)
            _atomic_write_json(state_dir / _STATE_FILENAME, state)

    return {
        "experiment_id": manifest["experiment_id"],
        "manifest_hash": manifest["candidate_manifest_sha256"],
        "forward_cutoff_utc": manifest["forward_cutoff_utc"],
        "bar_count_input": len(bars),
        "bar_count_after_dedupe": len(validated),
        "events_appended": len(collected),
        "dry_run": dry_run,
        "candidates": {
            cid: {
                "processed_through_utc": s.get("processed_through_utc"),
                "processed_forward_bar_count": s["processed_forward_bar_count"],
                "position_open": s["position_open"],
                "cash": s["cash"],
                "quantity": s["quantity"],
                "marked_equity": s["marked_equity"],
                "completed_trade_count": s["completed_trade_count"],
                "bullish_crossover_count": s["bullish_crossover_count"],
                "bullish_state_bar_count": s["bullish_state_bar_count"],
                "bullish_signal_count": s["bullish_signal_count"],
                "unique_bullish_episode_count": s["unique_bullish_episode_count"],
                "immediate_entry_count": s["immediate_entry_count"],
                "delayed_entry_count": s["delayed_entry_count"],
                "blocked_on_crossover_count": s["blocked_on_crossover_count"],
                "episodes_without_entry_count": s["episodes_without_entry_count"],
                "filter_evaluation_count": s["filter_evaluation_count"],
                "filter_allowed_count": s["filter_allowed_count"],
                "filter_blocked_count": s["filter_blocked_count"],
            }
            for cid, s in state["candidates"].items()
        },
        "research_only": True,
        "automatic_strategy_promotion_allowed": False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.tools.run_shadow_strategy_cycle",
        description=(
            "S62 forward-only shadow strategy validation. Read-only "
            "with respect to the paper strategy — no broker calls, "
            "no credentials, no scheduler changes."
        ),
    )
    p.add_argument("--state-dir", default=str(_DEFAULT_STATE_DIR),
                   help=f"Persistent state directory (default: {_DEFAULT_STATE_DIR}). "
                        "Never commit its contents.")
    p.add_argument("--cache-dir", default=str(_DEFAULT_CACHE_DIR),
                   help=f"Directory with SPY 60m cache bars (default: {_DEFAULT_CACHE_DIR}).")
    p.add_argument("--once", action="store_true",
                   help="Process any new bars once and exit (default action).")
    p.add_argument("--status", action="store_true",
                   help="Print current state without processing new bars.")
    p.add_argument("--dry-run", action="store_true",
                   help="Process in memory only; do not persist state or events.")
    p.add_argument("--json", action="store_true",
                   help="Only emit the JSON summary on stdout (no extra text).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    state_dir = Path(args.state_dir)
    now_utc = datetime.now(timezone.utc)
    try:
        if args.status:
            manifest = load_or_init_manifest(state_dir, now_utc)
            state = load_or_init_state(state_dir, manifest)
            print(json.dumps({
                "experiment_id": manifest["experiment_id"],
                "manifest_hash": manifest["candidate_manifest_sha256"],
                "forward_cutoff_utc": manifest["forward_cutoff_utc"],
                "candidates": state["candidates"],
                "research_only": True,
                "automatic_strategy_promotion_allowed": False,
            }, indent=2, default=str))
            return 0

        try:
            bars = load_cached_bars(Path(args.cache_dir), "SPY", "60m")
        except Exception as exc:
            raise ShadowError(f"could not load SPY 60m bars: {exc}") from exc

        summary = run_cycle(
            bars, state_dir=state_dir, now_utc=now_utc, dry_run=args.dry_run,
        )
    except ShadowError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
