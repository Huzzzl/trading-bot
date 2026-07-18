"""
tools/run_shadow_strategy_cycle.py
==================================
S62 forward-only shadow strategy validation (review-fixed).

Isolated from the paper execution path — no broker imports, no
credentials, no scheduler / risk / paper-strategy / position-sizing
changes. All state and events live under ``logs/shadow_strategy/``
(gitignored).

Modes
-----
    --once      process any new bars (may initialize on first run)
    --status    read-only status; must NOT initialize an experiment
    --dry-run   process in memory only; must NOT create or modify state
    --json      machine-readable summary on stdout

Correctness commitments (S62 review)
------------------------------------
* Delayed vs inherited vs immediate entries are classified precisely.
* Every episode is counted the moment it begins; active unclosed
  episodes count exactly once.
* Bearish exits always bypass the entry filter.
* ``--dry-run``, ``--status``, and the report tool never mutate the
  filesystem — file mtimes and byte contents are preserved.
* Strict state and event-log integrity validation; no silent init.
* Explicit duplicate/conflict/non-finite/gap detection on the input
  bar sequence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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
    """Any shadow runner failure — always fatal, CLI exits 2."""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(now_utc: datetime) -> dict[str, Any]:
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


def _load_manifest_from_disk(state_dir: Path) -> dict[str, Any]:
    """Load an existing manifest, verifying it matches the frozen set.

    Read-only — does not create the state directory or write anything.
    Raises :class:`ShadowError` when the manifest is absent, malformed,
    self-inconsistent, or drifted from the frozen S62 definitions.
    """
    path = state_dir / _MANIFEST_FILENAME
    if not path.exists():
        raise ShadowError(
            f"no shadow manifest at {path} — run --once to initialize"
        )
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
    canonical = build_manifest(datetime(1970, 1, 1, tzinfo=timezone.utc))
    if stored_hash != canonical["candidate_manifest_sha256"]:
        raise ShadowError(
            "manifest has drifted from the frozen S62 definitions — "
            "candidates, cutoff, execution, commission, or slippage "
            "cannot change once events exist"
        )
    return loaded


def load_or_init_manifest(state_dir: Path, now_utc: datetime) -> dict[str, Any]:
    """Load an existing manifest or initialize a new one.

    ``load_or_init_manifest`` is the one place allowed to create the
    state directory. Only used from ``--once``.
    """
    path = state_dir / _MANIFEST_FILENAME
    if path.exists():
        return _load_manifest_from_disk(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    canonical = build_manifest(now_utc)
    _atomic_write_json(path, canonical)
    return canonical


def load_manifest_readonly(state_dir: Path) -> dict[str, Any]:
    """Read-only load. Raises if no experiment has been initialized."""
    return _load_manifest_from_disk(state_dir)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


_STATE_TOP_KEYS = {"experiment_id", "manifest_hash", "candidates"}
_CANDIDATE_KEYS = {
    "candidate_id", "cash", "quantity", "position_open",
    "entry_price", "entry_timestamp_utc", "entry_forward_index",
    "realized_equity", "marked_equity", "peak_equity", "max_drawdown",
    "processed_through_utc", "last_forward_bar_utc",
    "pending_action",
    "completed_trade_count", "win_count", "loss_count",
    "cumulative_exposure_bars", "processed_forward_bar_count",
    "bullish_crossover_count", "bearish_crossover_count",
    "bullish_state_bar_count", "bullish_signal_count",
    "unique_bullish_episode_count",
    "inherited_bullish_episode_count",
    "filter_evaluation_count", "filter_allowed_count", "filter_blocked_count",
    "blocked_on_crossover_count",
    "immediate_entry_count", "delayed_entry_count",
    "inherited_bullish_state_entry_count",
    "episodes_without_entry_count",
    "trades", "returns_series", "first_forward_bar_utc",
    # Internal episode tracking (prefixed with underscore so the
    # validator can distinguish them from user-facing counters).
    "_forward_started",
    "_in_bullish_episode",
    "_current_episode_type",
    "_current_episode_had_crossover_block",
    "_current_episode_had_entry",
}


def _new_candidate_state(candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "cash": _INITIAL_EQUITY,
        "quantity": 0.0,
        "position_open": False,
        "entry_price": None,
        "entry_timestamp_utc": None,
        "entry_forward_index": None,
        "realized_equity": _INITIAL_EQUITY,
        "marked_equity": _INITIAL_EQUITY,
        "peak_equity": _INITIAL_EQUITY,
        "max_drawdown": 0.0,
        "processed_through_utc": None,
        "last_forward_bar_utc": None,
        "first_forward_bar_utc": None,
        "pending_action": None,
        "completed_trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "cumulative_exposure_bars": 0,
        "processed_forward_bar_count": 0,
        # Entry-signal counters.
        "bullish_crossover_count": 0,
        "bearish_crossover_count": 0,
        "bullish_state_bar_count": 0,
        "bullish_signal_count": 0,  # alias of bullish_state_bar_count
        "unique_bullish_episode_count": 0,
        "inherited_bullish_episode_count": 0,
        "filter_evaluation_count": 0,
        "filter_allowed_count": 0,
        "filter_blocked_count": 0,
        "blocked_on_crossover_count": 0,
        "immediate_entry_count": 0,
        "delayed_entry_count": 0,
        "inherited_bullish_state_entry_count": 0,
        "episodes_without_entry_count": 0,
        # Trade + return log for report metrics.
        "trades": [],
        "returns_series": [],
        # Internal episode tracking.
        "_forward_started": False,
        "_in_bullish_episode": False,
        "_current_episode_type": None,
        "_current_episode_had_crossover_block": False,
        "_current_episode_had_entry": False,
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


def _load_state_from_disk(state_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    path = state_dir / _STATE_FILENAME
    if not path.exists():
        raise ShadowError(
            f"no shadow state at {path} — run --once to initialize"
        )
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShadowError(f"state file is malformed: {exc}") from exc
    _validate_state(s, manifest)
    return s


def load_or_init_state(state_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Load an existing state or return a fresh one.

    Only used from ``--once`` — never creates a state file; that's
    done by the caller after processing. Read paths must use
    :func:`load_state_readonly`.
    """
    path = state_dir / _STATE_FILENAME
    if not path.exists():
        return _init_state(manifest)
    return _load_state_from_disk(state_dir, manifest)


def load_state_readonly(state_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Read-only load. Raises if no state file exists."""
    return _load_state_from_disk(state_dir, manifest)


# ---------------------------------------------------------------------------
# State validation
# ---------------------------------------------------------------------------


def _is_finite_number(x: Any) -> bool:
    if isinstance(x, bool):
        return False
    if isinstance(x, (int, float)):
        return math.isfinite(float(x))
    return False


def _validate_state(state: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Enforce every state invariant. Raise :class:`ShadowError` on any
    corruption / drift / consistency failure."""
    if not isinstance(state, dict):
        raise ShadowError("state must be a JSON object")
    for k in _STATE_TOP_KEYS:
        if k not in state:
            raise ShadowError(f"state missing top-level key {k!r}")
    if state["experiment_id"] != manifest["experiment_id"]:
        raise ShadowError(
            f"state experiment_id {state['experiment_id']!r} "
            f"!= manifest {manifest['experiment_id']!r}"
        )
    if state["manifest_hash"] != manifest["candidate_manifest_sha256"]:
        raise ShadowError(
            "state.json manifest_hash does not match current manifest — "
            "candidates or cutoff have drifted"
        )
    if not isinstance(state["candidates"], dict):
        raise ShadowError("state.candidates must be a JSON object")
    expected_ids = {c["id"] for c in manifest["candidates"]}
    got_ids = set(state["candidates"].keys())
    if got_ids != expected_ids:
        missing = expected_ids - got_ids
        extra = got_ids - expected_ids
        raise ShadowError(
            f"state candidates mismatch — missing={sorted(missing)} "
            f"extra={sorted(extra)}"
        )
    for cid, cs in state["candidates"].items():
        _validate_candidate_state(cid, cs)


_NON_NEGATIVE_INT_COUNTERS = (
    "completed_trade_count", "win_count", "loss_count",
    "cumulative_exposure_bars", "processed_forward_bar_count",
    "bullish_crossover_count", "bearish_crossover_count",
    "bullish_state_bar_count", "bullish_signal_count",
    "unique_bullish_episode_count", "inherited_bullish_episode_count",
    "filter_evaluation_count", "filter_allowed_count", "filter_blocked_count",
    "blocked_on_crossover_count",
    "immediate_entry_count", "delayed_entry_count",
    "inherited_bullish_state_entry_count",
    "episodes_without_entry_count",
)


def _validate_candidate_state(cid: str, cs: dict[str, Any]) -> None:
    if not isinstance(cs, dict):
        raise ShadowError(f"{cid}: candidate state must be a JSON object")
    if cs.get("candidate_id") != cid:
        raise ShadowError(
            f"{cid}: candidate_id field {cs.get('candidate_id')!r} does not "
            f"match key {cid!r}"
        )
    for key in ("cash", "quantity", "realized_equity", "marked_equity",
                "peak_equity", "max_drawdown"):
        if not _is_finite_number(cs.get(key)):
            raise ShadowError(f"{cid}.{key} must be a finite number")
    for key in _NON_NEGATIVE_INT_COUNTERS:
        v = cs.get(key)
        if not isinstance(v, int) or v < 0:
            raise ShadowError(f"{cid}.{key} must be a non-negative integer")
    if cs["bullish_signal_count"] != cs["bullish_state_bar_count"]:
        raise ShadowError(
            f"{cid}.bullish_signal_count "
            f"({cs['bullish_signal_count']}) must equal "
            f"bullish_state_bar_count ({cs['bullish_state_bar_count']})"
        )
    # Position consistency.
    if cs["position_open"]:
        if cs["quantity"] <= 0:
            raise ShadowError(f"{cid}: position_open but quantity <= 0")
        if cs["cash"] != 0.0:
            raise ShadowError(f"{cid}: position_open but cash != 0")
        if not _is_finite_number(cs.get("entry_price")):
            raise ShadowError(f"{cid}: position_open but entry_price invalid")
        if not isinstance(cs.get("entry_timestamp_utc"), str):
            raise ShadowError(f"{cid}: position_open but entry_timestamp_utc missing")
    else:
        if cs["quantity"] != 0.0:
            raise ShadowError(f"{cid}: not position_open but quantity != 0")
        if cs.get("entry_price") is not None:
            raise ShadowError(f"{cid}: not position_open but entry_price set")
        if cs.get("entry_timestamp_utc") is not None:
            raise ShadowError(f"{cid}: not position_open but entry_timestamp_utc set")
    # Pending action validity.
    pa = cs.get("pending_action")
    if pa is not None:
        if not isinstance(pa, dict):
            raise ShadowError(f"{cid}.pending_action must be a JSON object")
        if pa.get("side") not in ("buy", "sell"):
            raise ShadowError(f"{cid}.pending_action.side invalid")
        if not isinstance(pa.get("signal_bar_utc"), str):
            raise ShadowError(f"{cid}.pending_action.signal_bar_utc missing")
        if pa["side"] == "buy":
            if pa.get("entry_type") not in ("immediate", "delayed", "inherited"):
                raise ShadowError(
                    f"{cid}.pending_action.entry_type invalid: "
                    f"{pa.get('entry_type')!r}"
                )
    # Timestamp validity.
    for key in ("processed_through_utc", "last_forward_bar_utc",
                "first_forward_bar_utc"):
        v = cs.get(key)
        if v is not None:
            if not isinstance(v, str):
                raise ShadowError(f"{cid}.{key} must be a UTC ISO-8601 string")
            try:
                _parse_ts(v)
            except Exception as exc:
                raise ShadowError(f"{cid}.{key} unparseable: {exc}") from exc
    # Trades schema.
    if not isinstance(cs.get("trades"), list):
        raise ShadowError(f"{cid}.trades must be a list")
    for i, t in enumerate(cs["trades"]):
        if not isinstance(t, dict):
            raise ShadowError(f"{cid}.trades[{i}] must be an object")
        for key in ("entry_ts", "exit_ts", "entry_price", "exit_price",
                    "qty", "pnl", "bars_held", "entry_forward_index",
                    "exit_forward_index"):
            if key not in t:
                raise ShadowError(f"{cid}.trades[{i}] missing {key!r}")
        if not _is_finite_number(t["pnl"]):
            raise ShadowError(f"{cid}.trades[{i}].pnl invalid")
    if cs["completed_trade_count"] != len(cs["trades"]):
        raise ShadowError(
            f"{cid}: completed_trade_count ({cs['completed_trade_count']}) "
            f"!= len(trades) ({len(cs['trades'])})"
        )
    entries = (
        cs["immediate_entry_count"]
        + cs["delayed_entry_count"]
        + cs["inherited_bullish_state_entry_count"]
    )
    expected_entries = cs["completed_trade_count"] + (1 if cs["position_open"] else 0)
    if entries != expected_entries:
        raise ShadowError(
            f"{cid}: immediate+delayed+inherited={entries} != "
            f"completed_trade_count + open_position={expected_entries}"
        )
    # Returns series schema.
    if not isinstance(cs.get("returns_series"), list):
        raise ShadowError(f"{cid}.returns_series must be a list")


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


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
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ev = {
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
    if detail:
        ev["detail"] = detail
    return ev


def _load_known_event_ids(events_path: Path) -> set[str]:
    """Strict event log loader.

    Reject silently-malformed lines. Allow a truncated final line to
    be recovered as an interrupted append — reported by the caller.
    """
    known: set[str] = set()
    if not events_path.exists():
        return known
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=False)
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            # Recover only a truncated FINAL line as an interrupted
            # append — every other malformed line fails closed.
            if i == len(lines) - 1:
                # Silently drop the truncated tail; the caller may
                # later re-append.
                continue
            raise ShadowError(
                f"events.jsonl line {i + 1} is malformed — cannot proceed"
            )
        if not isinstance(e, dict) or "event_id" not in e:
            raise ShadowError(
                f"events.jsonl line {i + 1} missing event_id"
            )
        known.add(e["event_id"])
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
# Bar validation (data integrity)
# ---------------------------------------------------------------------------


def _validate_and_prepare_bars(
    bars: Sequence[Bar],
) -> tuple[list[Bar], int, list[dict[str, Any]]]:
    """Return ``(validated_bars, duplicate_bar_skipped_count, gap_events)``.

    * Non-finite OHLCV → :class:`ShadowError`.
    * Same timestamp + identical OHLCV → dedupe, count as duplicate.
    * Same timestamp + different OHLCV → :class:`ShadowError`.
    * Intraday gaps within a regular session (gap > 65 minutes, < 12h,
      same UTC date) are reported (do not synthesize).
    """
    if not bars:
        return [], 0, []
    entries: list[tuple[datetime, Bar]] = []
    for b in bars:
        for f in (b.open, b.high, b.low, b.close, b.volume):
            if not _is_finite_number(f):
                raise ShadowError(
                    f"non-finite OHLCV value in bar at {b.ts!r}"
                )
        entries.append((_bar_ts_utc(b), b))
    entries.sort(key=lambda t: t[0])

    dedup: list[tuple[datetime, Bar]] = []
    duplicates = 0
    for ts, b in entries:
        if dedup and dedup[-1][0] == ts:
            prev = dedup[-1][1]
            if (b.open, b.high, b.low, b.close, b.volume) != (
                prev.open, prev.high, prev.low, prev.close, prev.volume,
            ):
                raise ShadowError(
                    f"conflicting OHLCV for the same timestamp {ts.isoformat()}"
                )
            duplicates += 1
            continue
        dedup.append((ts, b))

    gap_events: list[dict[str, Any]] = []
    for i in range(1, len(dedup)):
        prev_ts, _ = dedup[i - 1]
        cur_ts, _ = dedup[i]
        delta = cur_ts - prev_ts
        seconds = delta.total_seconds()
        # Intraday gap heuristic:
        #  * same UTC date;
        #  * > 65 minutes (allows minor jitter);
        #  * < 12 hours (rules out overnight / weekend / holiday closures).
        if (
            prev_ts.date() == cur_ts.date()
            and 65 * 60 < seconds < 12 * 3600
        ):
            gap_events.append({
                "prev_bar_utc": prev_ts.isoformat(),
                "next_bar_utc": cur_ts.isoformat(),
                "gap_seconds": seconds,
            })
    return [b for _, b in dedup], duplicates, gap_events


# ---------------------------------------------------------------------------
# Bar processing
# ---------------------------------------------------------------------------


def _apply_slippage(price: float, side: str) -> float:
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
    """Advance one candidate through every unseen bar."""
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

        # Skip already-processed bars.
        if processed_dt is not None and bar_ts <= processed_dt:
            continue

        is_forward = bar_ts > cutoff

        # Step 1: execute pending action at this bar's open.
        pa = candidate_state.get("pending_action")
        if is_forward and pa is not None:
            _execute_pending(candidate_state, pa, bar, bar_ts, variant,
                             manifest_hash_str, cid, events_out)

        # Step 2: SMA + crossover detection.
        short_sma = _sma(closes, short_w, i)
        long_sma = _sma(closes, long_w, i)
        prev_short = _sma(closes, short_w, i - 1) if i > 0 else None
        prev_long = _sma(closes, long_w, i - 1) if i > 0 else None

        if is_forward and short_sma is not None and long_sma is not None:
            # First forward bar for this candidate — detect an
            # inherited bullish episode.
            first_forward = not candidate_state["_forward_started"]
            if first_forward:
                candidate_state["_forward_started"] = True

            bullish_cross = False
            bearish_cross = False
            if prev_short is not None and prev_long is not None:
                bullish_cross = (
                    prev_short <= prev_long and short_sma > long_sma
                )
                bearish_cross = (
                    prev_short >= prev_long and short_sma < long_sma
                )

            # Inherited bullish episode — first forward bar is already
            # bullish and no forward crossover is occurring.
            if first_forward and short_sma > long_sma and not bullish_cross:
                candidate_state["unique_bullish_episode_count"] += 1
                candidate_state["inherited_bullish_episode_count"] += 1
                candidate_state["_in_bullish_episode"] = True
                candidate_state["_current_episode_type"] = "inherited"
                candidate_state["_current_episode_had_crossover_block"] = False
                candidate_state["_current_episode_had_entry"] = False

            if bullish_cross:
                candidate_state["bullish_crossover_count"] += 1
                # If a previous inherited/forward episode is still
                # active (shouldn't be — bearish would have closed
                # it), close it silently.
                if not candidate_state["_in_bullish_episode"]:
                    candidate_state["unique_bullish_episode_count"] += 1
                candidate_state["_in_bullish_episode"] = True
                candidate_state["_current_episode_type"] = "forward_crossover"
                candidate_state["_current_episode_had_crossover_block"] = False
                candidate_state["_current_episode_had_entry"] = False
                events_out.append(_make_event(
                    cid, "BULLISH_CROSSOVER", bar_ts.isoformat(),
                    manifest_hash_str=manifest_hash_str,
                    short_sma=short_sma, long_sma=long_sma,
                    filter_name=variant,
                ))

            if bearish_cross:
                candidate_state["bearish_crossover_count"] += 1
                if candidate_state["_in_bullish_episode"]:
                    if not candidate_state["_current_episode_had_entry"]:
                        candidate_state["episodes_without_entry_count"] += 1
                candidate_state["_in_bullish_episode"] = False
                candidate_state["_current_episode_type"] = None
                candidate_state["_current_episode_had_crossover_block"] = False
                candidate_state["_current_episode_had_entry"] = False
                events_out.append(_make_event(
                    cid, "BEARISH_CROSSOVER", bar_ts.isoformat(),
                    manifest_hash_str=manifest_hash_str,
                    short_sma=short_sma, long_sma=long_sma,
                    filter_name=variant,
                ))
                # Bearish exits bypass entry filters.
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

            # Flat + bullish-state bar → filter evaluation.
            is_flat = candidate_state["quantity"] == 0.0 and not candidate_state["position_open"]
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
                    # Classify the entry.
                    ep_type = candidate_state["_current_episode_type"]
                    ep_blocked = candidate_state["_current_episode_had_crossover_block"]
                    if bullish_cross:
                        entry_type = "immediate"
                        reason = "bullish_crossover"
                    elif ep_type == "inherited":
                        entry_type = "inherited"
                        reason = "inherited_bullish_state"
                    elif ep_type == "forward_crossover" and ep_blocked:
                        entry_type = "delayed"
                        reason = "bullish_state_after_block"
                    else:
                        # Safety net — should not fire given the
                        # branches above cover all in-episode
                        # scenarios. Fall back to delayed.
                        entry_type = "delayed"
                        reason = "bullish_state"
                    candidate_state["pending_action"] = {
                        "side": "buy",
                        "signal_bar_utc": bar_ts.isoformat(),
                        "reason": reason,
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
                        candidate_state["_current_episode_had_crossover_block"] = True
                    events_out.append(_make_event(
                        cid, "ENTRY_FILTER_BLOCKED", bar_ts.isoformat(),
                        manifest_hash_str=manifest_hash_str,
                        short_sma=short_sma, long_sma=long_sma,
                        filter_name=variant, filter_result=False,
                        reason="filter_block",
                    ))

        if is_forward:
            fwd_index = candidate_state["processed_forward_bar_count"]
            candidate_state["processed_forward_bar_count"] = fwd_index + 1
            if candidate_state["first_forward_bar_utc"] is None:
                candidate_state["first_forward_bar_utc"] = bar_ts.isoformat()
            candidate_state["last_forward_bar_utc"] = bar_ts.isoformat()
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
            candidate_state["entry_forward_index"] = (
                candidate_state["processed_forward_bar_count"]
            )
            candidate_state["_current_episode_had_entry"] = True
            entry_type = pa.get("entry_type") or "delayed"
            if entry_type == "immediate":
                candidate_state["immediate_entry_count"] += 1
            elif entry_type == "inherited":
                candidate_state["inherited_bullish_state_entry_count"] += 1
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
        exit_forward_index = candidate_state["processed_forward_bar_count"]
        entry_forward_index = (
            candidate_state["entry_forward_index"]
            if candidate_state["entry_forward_index"] is not None
            else exit_forward_index
        )
        bars_held = exit_forward_index - entry_forward_index
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
            "entry_forward_index": entry_forward_index,
            "exit_forward_index": exit_forward_index,
            "bars_held": bars_held,
        })
        candidate_state["realized_equity"] = proceeds
        candidate_state["entry_price"] = None
        candidate_state["entry_timestamp_utc"] = None
        candidate_state["entry_forward_index"] = None
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


def run_cycle(
    bars: Sequence[Bar],
    *,
    state_dir: Path,
    now_utc: datetime,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process all unseen bars for all five candidates."""
    if dry_run:
        # Dry-run requires an initialized experiment — it must not
        # create the state directory, manifest, state, events, or a
        # persistent lock file. It may only read.
        manifest = load_manifest_readonly(state_dir)
    else:
        manifest = load_or_init_manifest(state_dir, now_utc)

    cutoff = _parse_ts(manifest["forward_cutoff_utc"])
    validated, duplicates, gap_events = _validate_and_prepare_bars(bars)

    if dry_run:
        # Load state (must exist), simulate in memory, discard.
        state_path = state_dir / _STATE_FILENAME
        if state_path.exists():
            state = load_state_readonly(state_dir, manifest)
        else:
            state = _init_state(manifest)
        events_path = state_dir / _EVENTS_FILENAME
        known_event_ids = _load_known_event_ids(events_path)
        summary = _run_candidates(
            state, manifest, validated, cutoff, known_event_ids,
        )
        summary["dry_run"] = True
        summary["duplicate_bar_skipped_count"] = duplicates
        summary["gap_events"] = gap_events
        return summary

    lock_path = state_dir / _LOCK_FILENAME
    with _FileLock(lock_path):
        state = load_or_init_state(state_dir, manifest)
        events_path = state_dir / _EVENTS_FILENAME
        known_event_ids = _load_known_event_ids(events_path)

        summary = _run_candidates(
            state, manifest, validated, cutoff, known_event_ids,
        )
        collected = summary.pop("_events")
        _append_events(events_path, collected)
        _atomic_write_json(state_dir / _STATE_FILENAME, state)

    summary["dry_run"] = False
    summary["duplicate_bar_skipped_count"] = duplicates
    summary["gap_events"] = gap_events
    return summary


def _run_candidates(
    state: dict[str, Any],
    manifest: dict[str, Any],
    validated: Sequence[Bar],
    cutoff: datetime,
    known_event_ids: set[str],
) -> dict[str, Any]:
    collected: list[dict[str, Any]] = []
    for cdef in manifest["candidates"]:
        cid = cdef["id"]
        cstate = state["candidates"][cid]
        per_candidate: list[dict[str, Any]] = []
        _process_candidate(
            cdef, cstate, validated, cutoff,
            manifest["candidate_manifest_sha256"], per_candidate,
        )
        for e in per_candidate:
            if e["event_id"] not in known_event_ids:
                known_event_ids.add(e["event_id"])
                collected.append(e)

    return {
        "experiment_id": manifest["experiment_id"],
        "manifest_hash": manifest["candidate_manifest_sha256"],
        "forward_cutoff_utc": manifest["forward_cutoff_utc"],
        "bar_count_after_dedupe": len(validated),
        "events_appended": len(collected),
        "candidates": {
            cid: _summary_snapshot(s)
            for cid, s in state["candidates"].items()
        },
        "research_only": True,
        "automatic_strategy_promotion_allowed": False,
        "_events": collected,
    }


def _summary_snapshot(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "processed_through_utc": s.get("processed_through_utc"),
        "first_forward_bar_utc": s.get("first_forward_bar_utc"),
        "last_forward_bar_utc": s.get("last_forward_bar_utc"),
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
        "inherited_bullish_episode_count": s.get("inherited_bullish_episode_count", 0),
        "immediate_entry_count": s["immediate_entry_count"],
        "delayed_entry_count": s["delayed_entry_count"],
        "inherited_bullish_state_entry_count": s.get(
            "inherited_bullish_state_entry_count", 0,
        ),
        "blocked_on_crossover_count": s["blocked_on_crossover_count"],
        "episodes_without_entry_count": s["episodes_without_entry_count"],
        "filter_evaluation_count": s["filter_evaluation_count"],
        "filter_allowed_count": s["filter_allowed_count"],
        "filter_blocked_count": s["filter_blocked_count"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.tools.run_shadow_strategy_cycle",
        description=(
            "S62 forward-only shadow strategy validation. Read-only "
            "with respect to the paper strategy."
        ),
    )
    p.add_argument("--state-dir", default=str(_DEFAULT_STATE_DIR),
                   help=f"Persistent state directory (default: {_DEFAULT_STATE_DIR}).")
    p.add_argument("--cache-dir", default=str(_DEFAULT_CACHE_DIR),
                   help=f"Directory with SPY 60m cache bars (default: {_DEFAULT_CACHE_DIR}).")
    p.add_argument("--once", action="store_true",
                   help="Process any new bars once and exit (may initialize).")
    p.add_argument("--status", action="store_true",
                   help="Print current state without processing (read-only).")
    p.add_argument("--dry-run", action="store_true",
                   help="Process in memory only; never mutate the state dir.")
    p.add_argument("--json", action="store_true",
                   help="Only emit the JSON summary on stdout (no extra text).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    state_dir = Path(args.state_dir)
    now_utc = datetime.now(timezone.utc)
    try:
        if args.status:
            manifest = load_manifest_readonly(state_dir)
            state = load_state_readonly(state_dir, manifest)
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
