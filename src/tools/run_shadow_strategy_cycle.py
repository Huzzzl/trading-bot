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
    corruption / drift / consistency failure. Exact schema equality —
    missing OR unexpected top-level keys are rejected."""
    if not isinstance(state, dict):
        raise ShadowError("state must be a JSON object")
    got_top = set(state.keys())
    missing_top = _STATE_TOP_KEYS - got_top
    extra_top = got_top - _STATE_TOP_KEYS
    if missing_top or extra_top:
        raise ShadowError(
            f"state top-level schema mismatch — missing={sorted(missing_top)} "
            f"extra={sorted(extra_top)}"
        )
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


_ALLOWED_EPISODE_TYPES = (None, "inherited", "forward_crossover")
_ALLOWED_ENTRY_TYPES = ("immediate", "delayed", "inherited")
_ALLOWED_BUY_REASONS = (
    "bullish_crossover", "inherited_bullish_state",
    "bullish_state_after_block",
)
_ALLOWED_SELL_REASONS = ("bearish_crossover",)


def _check_ts(cid: str, key: str, v: Any) -> None:
    if not isinstance(v, str):
        raise ShadowError(f"{cid}.{key} must be a UTC ISO-8601 string")
    try:
        _parse_ts(v)
    except Exception as exc:
        raise ShadowError(f"{cid}.{key} unparseable: {exc}") from exc


def _validate_candidate_state(cid: str, cs: dict[str, Any]) -> None:
    if not isinstance(cs, dict):
        raise ShadowError(f"{cid}: candidate state must be a JSON object")
    got = set(cs.keys())
    missing = _CANDIDATE_KEYS - got
    extra = got - _CANDIDATE_KEYS
    if missing or extra:
        raise ShadowError(
            f"{cid}: candidate schema mismatch — "
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )
    if cs["candidate_id"] != cid:
        raise ShadowError(
            f"{cid}: candidate_id field {cs['candidate_id']!r} does not "
            f"match key {cid!r}"
        )
    for key in ("cash", "quantity", "realized_equity", "marked_equity",
                "peak_equity", "max_drawdown"):
        if not _is_finite_number(cs[key]):
            raise ShadowError(f"{cid}.{key} must be a finite number")
    for key in _NON_NEGATIVE_INT_COUNTERS:
        v = cs[key]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ShadowError(f"{cid}.{key} must be a non-negative integer")
    if cs["bullish_signal_count"] != cs["bullish_state_bar_count"]:
        raise ShadowError(
            f"{cid}.bullish_signal_count "
            f"({cs['bullish_signal_count']}) must equal "
            f"bullish_state_bar_count ({cs['bullish_state_bar_count']})"
        )
    # Internal episode fields — strict types.
    if not isinstance(cs["_forward_started"], bool):
        raise ShadowError(f"{cid}._forward_started must be bool")
    if not isinstance(cs["_in_bullish_episode"], bool):
        raise ShadowError(f"{cid}._in_bullish_episode must be bool")
    if cs["_current_episode_type"] not in _ALLOWED_EPISODE_TYPES:
        raise ShadowError(
            f"{cid}._current_episode_type invalid: "
            f"{cs['_current_episode_type']!r}"
        )
    for key in ("_current_episode_had_crossover_block",
                "_current_episode_had_entry"):
        if not isinstance(cs[key], bool):
            raise ShadowError(f"{cid}.{key} must be bool")
    # entry_forward_index null or non-negative int.
    efi = cs["entry_forward_index"]
    if efi is not None:
        if not isinstance(efi, int) or isinstance(efi, bool) or efi < 0:
            raise ShadowError(
                f"{cid}.entry_forward_index must be null or "
                f"non-negative int, got {efi!r}"
            )
    # Position consistency.
    if cs["position_open"]:
        if cs["quantity"] <= 0:
            raise ShadowError(f"{cid}: position_open but quantity <= 0")
        if cs["cash"] != 0.0:
            raise ShadowError(f"{cid}: position_open but cash != 0")
        if not _is_finite_number(cs["entry_price"]):
            raise ShadowError(f"{cid}: position_open but entry_price invalid")
        if not isinstance(cs["entry_timestamp_utc"], str):
            raise ShadowError(f"{cid}: position_open but entry_timestamp_utc missing")
        _check_ts(cid, "entry_timestamp_utc", cs["entry_timestamp_utc"])
        if efi is None:
            raise ShadowError(
                f"{cid}: position_open but entry_forward_index is null"
            )
    else:
        if cs["quantity"] != 0.0:
            raise ShadowError(f"{cid}: not position_open but quantity != 0")
        if cs["entry_price"] is not None:
            raise ShadowError(f"{cid}: not position_open but entry_price set")
        if cs["entry_timestamp_utc"] is not None:
            raise ShadowError(f"{cid}: not position_open but entry_timestamp_utc set")
        if efi is not None:
            raise ShadowError(
                f"{cid}: not position_open but entry_forward_index set"
            )
    # Pending action validity.
    pa = cs["pending_action"]
    if pa is not None:
        if not isinstance(pa, dict):
            raise ShadowError(f"{cid}.pending_action must be a JSON object")
        if pa.get("side") not in ("buy", "sell"):
            raise ShadowError(f"{cid}.pending_action.side invalid")
        sig_ts = pa.get("signal_bar_utc")
        if not isinstance(sig_ts, str):
            raise ShadowError(f"{cid}.pending_action.signal_bar_utc missing")
        try:
            _parse_ts(sig_ts)
        except Exception as exc:
            raise ShadowError(
                f"{cid}.pending_action.signal_bar_utc unparseable: {exc}"
            ) from exc
        if pa["side"] == "buy":
            if pa.get("entry_type") not in _ALLOWED_ENTRY_TYPES:
                raise ShadowError(
                    f"{cid}.pending_action.entry_type invalid: "
                    f"{pa.get('entry_type')!r}"
                )
            if pa.get("reason") not in _ALLOWED_BUY_REASONS:
                raise ShadowError(
                    f"{cid}.pending_action.reason invalid for buy: "
                    f"{pa.get('reason')!r}"
                )
        else:
            if pa.get("reason") not in _ALLOWED_SELL_REASONS:
                raise ShadowError(
                    f"{cid}.pending_action.reason invalid for sell: "
                    f"{pa.get('reason')!r}"
                )
    # Timestamp validity.
    processed = cs["processed_through_utc"]
    last_fwd = cs["last_forward_bar_utc"]
    first_fwd = cs["first_forward_bar_utc"]
    if processed is not None:
        _check_ts(cid, "processed_through_utc", processed)
    if last_fwd is not None:
        _check_ts(cid, "last_forward_bar_utc", last_fwd)
    if first_fwd is not None:
        _check_ts(cid, "first_forward_bar_utc", first_fwd)

    # Forward timestamps must accompany forward-bar counts.
    fwd_count = cs["processed_forward_bar_count"]
    if fwd_count == 0:
        if first_fwd is not None or last_fwd is not None:
            raise ShadowError(
                f"{cid}: zero forward bars but first/last forward "
                f"timestamps are not null"
            )
    else:
        if first_fwd is None or last_fwd is None:
            raise ShadowError(
                f"{cid}: {fwd_count} forward bars but first_forward_bar_utc "
                f"or last_forward_bar_utc is null"
            )
        # Ordering: first <= last <= processed_through.
        if _parse_ts(first_fwd) > _parse_ts(last_fwd):
            raise ShadowError(
                f"{cid}: first_forward_bar_utc > last_forward_bar_utc"
            )
        if processed is None or _parse_ts(last_fwd) > _parse_ts(processed):
            raise ShadowError(
                f"{cid}: last_forward_bar_utc > processed_through_utc"
            )
    # Trades schema.
    trades = cs["trades"]
    if not isinstance(trades, list):
        raise ShadowError(f"{cid}.trades must be a list")
    for i, t in enumerate(trades):
        if not isinstance(t, dict):
            raise ShadowError(f"{cid}.trades[{i}] must be an object")
        for key in ("entry_ts", "exit_ts", "entry_price", "exit_price",
                    "qty", "pnl", "bars_held", "entry_forward_index",
                    "exit_forward_index"):
            if key not in t:
                raise ShadowError(f"{cid}.trades[{i}] missing {key!r}")
        _check_ts(cid, f"trades[{i}].entry_ts", t["entry_ts"])
        _check_ts(cid, f"trades[{i}].exit_ts", t["exit_ts"])
        for key in ("entry_price", "exit_price", "qty", "pnl"):
            if not _is_finite_number(t[key]):
                raise ShadowError(f"{cid}.trades[{i}].{key} not finite")
        for key in ("bars_held", "entry_forward_index", "exit_forward_index"):
            v = t[key]
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ShadowError(
                    f"{cid}.trades[{i}].{key} must be non-negative int"
                )
        if t["exit_forward_index"] < t["entry_forward_index"]:
            raise ShadowError(
                f"{cid}.trades[{i}]: exit_forward_index < entry_forward_index"
            )
        if t["bars_held"] != t["exit_forward_index"] - t["entry_forward_index"]:
            raise ShadowError(
                f"{cid}.trades[{i}]: bars_held "
                f"({t['bars_held']}) does not equal exit - entry "
                f"({t['exit_forward_index'] - t['entry_forward_index']})"
            )
    if cs["completed_trade_count"] != len(trades):
        raise ShadowError(
            f"{cid}: completed_trade_count ({cs['completed_trade_count']}) "
            f"!= len(trades) ({len(trades)})"
        )
    if cs["win_count"] + cs["loss_count"] != cs["completed_trade_count"]:
        raise ShadowError(
            f"{cid}: win_count + loss_count "
            f"({cs['win_count'] + cs['loss_count']}) != "
            f"completed_trade_count ({cs['completed_trade_count']})"
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
    rs = cs["returns_series"]
    if not isinstance(rs, list):
        raise ShadowError(f"{cid}.returns_series must be a list")
    prev_ts = None
    for i, r in enumerate(rs):
        if not isinstance(r, dict):
            raise ShadowError(f"{cid}.returns_series[{i}] must be an object")
        if set(r.keys()) != {"ts", "r"}:
            raise ShadowError(
                f"{cid}.returns_series[{i}] must have exactly keys {{ts, r}}"
            )
        _check_ts(cid, f"returns_series[{i}].ts", r["ts"])
        if not _is_finite_number(r["r"]):
            raise ShadowError(
                f"{cid}.returns_series[{i}].r must be a finite number"
            )
        cur_ts = _parse_ts(r["ts"])
        if prev_ts is not None and cur_ts < prev_ts:
            raise ShadowError(
                f"{cid}.returns_series[{i}].ts regresses vs previous entry"
            )
        prev_ts = cur_ts
    # Episode & filter counter relationships.
    if (
        cs["unique_bullish_episode_count"]
        < cs["bullish_crossover_count"]
    ):
        raise ShadowError(
            f"{cid}: unique_bullish_episode_count < bullish_crossover_count"
        )
    if (
        cs["unique_bullish_episode_count"]
        < cs["inherited_bullish_episode_count"]
    ):
        raise ShadowError(
            f"{cid}: unique_bullish_episode_count < inherited_bullish_episode_count"
        )
    if (
        cs["filter_allowed_count"] + cs["filter_blocked_count"]
        != cs["filter_evaluation_count"]
    ):
        raise ShadowError(
            f"{cid}: filter_allowed + filter_blocked != filter_evaluation_count"
        )
    if cs["blocked_on_crossover_count"] > cs["filter_blocked_count"]:
        raise ShadowError(
            f"{cid}: blocked_on_crossover_count > filter_blocked_count"
        )


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


def _validate_event_record(
    e: Any,
    line_no: int,
    *,
    allowed_cids: frozenset[str] | set[str],
    expected_exp: str,
    expected_hash: str,
) -> str:
    """Validate one decoded JSONL event object against the full S62
    event schema. Returns its ``event_id`` on success; raises
    :class:`ShadowError` on any anomaly.

    This is the single semantic-validation implementation used by
    every path that reads events.jsonl — no runtime path may use a
    weaker parse-only check.
    """
    if not isinstance(e, dict):
        raise ShadowError(f"events.jsonl line {line_no} is not an object")
    eid = e.get("event_id")
    if not isinstance(eid, str) or not eid:
        raise ShadowError(
            f"events.jsonl line {line_no} event_id invalid: {eid!r}"
        )
    if e.get("experiment_id") != expected_exp:
        raise ShadowError(
            f"events.jsonl line {line_no} experiment_id "
            f"{e.get('experiment_id')!r} != manifest {expected_exp!r}"
        )
    if e.get("manifest_hash") != expected_hash:
        raise ShadowError(
            f"events.jsonl line {line_no} manifest_hash "
            f"{e.get('manifest_hash')!r} does not match current manifest"
        )
    cid = e.get("candidate_id")
    if cid not in allowed_cids:
        raise ShadowError(
            f"events.jsonl line {line_no} unknown candidate_id {cid!r}"
        )
    et = e.get("event_type")
    if et not in _ALLOWED_EVENT_TYPES:
        raise ShadowError(
            f"events.jsonl line {line_no} unknown event_type {et!r}"
        )
    for key in ("signal_bar_utc", "event_timestamp_utc"):
        v = e.get(key)
        if not isinstance(v, str):
            raise ShadowError(
                f"events.jsonl line {line_no} {key} must be a UTC "
                f"ISO-8601 string, got {type(v).__name__}"
            )
        try:
            _parse_ts(v)
        except Exception as exc:
            raise ShadowError(
                f"events.jsonl line {line_no} {key} unparseable: {exc}"
            ) from exc
    exec_ts = e.get("execution_bar_utc")
    if exec_ts is not None:
        if not isinstance(exec_ts, str):
            raise ShadowError(
                f"events.jsonl line {line_no} execution_bar_utc must be "
                f"a UTC string when non-null"
            )
        try:
            _parse_ts(exec_ts)
        except Exception as exc:
            raise ShadowError(
                f"events.jsonl line {line_no} execution_bar_utc "
                f"unparseable: {exc}"
            ) from exc
    for key in _NUMERIC_EVENT_FIELDS:
        v = e.get(key)
        if v is None:
            continue
        if not _is_finite_number(v):
            raise ShadowError(
                f"events.jsonl line {line_no} {key} must be null or "
                f"a finite number, got {v!r}"
            )
    fr = e.get("filter_result")
    if fr is not None and not isinstance(fr, bool):
        raise ShadowError(
            f"events.jsonl line {line_no} filter_result must be null "
            f"or bool, got {type(fr).__name__}"
        )
    ps = e.get("position_state")
    if ps is not None and ps not in _ALLOWED_POSITION_STATES:
        raise ShadowError(
            f"events.jsonl line {line_no} position_state invalid: {ps!r}"
        )
    return eid


def _validate_and_load_event_ids(
    events_path: Path,
    manifest: dict[str, Any],
    *,
    require_terminated: bool = True,
) -> set[str]:
    """Canonical, fail-closed event-log loader + semantic validator.

    Validates UTF-8 decodability, JSONL framing, and the full S62
    event schema (experiment_id, manifest_hash, candidate_id,
    event_type, timestamps, numeric/enum fields) for every complete
    physical line, and rejects duplicate event IDs. This is the ONLY
    event-log loader in the module — every runtime path (normal
    ``--once``, ``--dry-run``, ``--status``, the report tool, the
    post-repair revalidation, and any future crash-recovery /
    dedup path) must call this function. No path may use a weaker
    parse-only loader.

    ``require_terminated``:

    * ``True`` (default — every read path and the final state after
      any mutation): the raw file must end with ``b"\\n"``. A missing
      trailing newline — whether the final record is well-formed or
      not — raises :class:`ShadowError`, since appending onto such a
      file would concatenate a fresh record onto the previous line.
    * ``False`` (used only by the internal pre-repair inspection
      step): tolerates a missing trailing newline. Every *complete*
      (newline-terminated) line is still validated as usual. If the
      final incomplete fragment happens to parse as JSON, it is
      ALSO semantically validated and included in the returned ID
      set — and a semantic failure there still raises, matching the
      requirement that a "valid but semantically wrong" unterminated
      record must be rejected before any repair mutation. A fragment
      that fails to parse at all is silently excluded — the caller
      (the repair inspector) decides what to do with it.

    Returns the set of validated, unique event IDs.
    """
    known: set[str] = set()
    if not events_path.exists():
        return known
    raw = events_path.read_bytes()
    if not raw:
        return known
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ShadowError(f"events.jsonl is not valid UTF-8: {exc}") from exc

    ends_with_newline = raw.endswith(b"\n")
    if require_terminated and not ends_with_newline:
        raise ShadowError(
            "events.jsonl does not end with a newline — appending "
            "another event would concatenate it onto the previous "
            "record and silently corrupt the log. "
            "Run --repair-event-log-tail to normalize the terminator."
        )

    lines = text.splitlines(keepends=False)
    complete_count = len(lines) if ends_with_newline else max(len(lines) - 1, 0)

    allowed_cids = {_EXPERIMENT_CANDIDATE_ID} | {
        c["id"] for c in manifest["candidates"]
    }
    expected_exp = manifest["experiment_id"]
    expected_hash = manifest["candidate_manifest_sha256"]

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        is_complete = i < complete_count
        try:
            e = json.loads(stripped)
        except json.JSONDecodeError as exc:
            if is_complete:
                raise ShadowError(
                    f"events.jsonl line {i + 1} is malformed ({exc.msg})"
                ) from exc
            # Final incomplete fragment that fails to parse at all —
            # the caller (repair inspector) decides whether this is
            # the specifically-permitted malformed-tail case. Not
            # registered in the returned ID set.
            continue
        eid = _validate_event_record(
            e, i + 1,
            allowed_cids=allowed_cids,
            expected_exp=expected_exp,
            expected_hash=expected_hash,
        )
        if eid in known:
            raise ShadowError(
                f"events.jsonl line {i + 1} duplicate event_id {eid!r}"
            )
        known.add(eid)
    return known


def _repair_event_log_tail(
    events_path: Path,
    *,
    allow_terminator_restore: bool = True,
) -> dict[str, Any] | None:
    """Repair one of two specific framing anomalies:

    1. **Unterminated MALFORMED tail** — file does not end in ``b"\\n"``
       and the final line fails to parse. Rewrite the file with the
       malformed tail removed. Detail includes ``removed_byte_count``,
       ``removed_tail_length``, ``removed_tail_sha256`` and
       ``previous_event_id``. Kind = ``"tail_removed"``.

    2. **Unterminated VALID tail** — file does not end in ``b"\\n"``
       and the final line IS a valid JSON record. Preserve the
       record and add a single newline byte. Detail includes
       ``pre_repair_file_sha256``, ``previous_event_id``,
       ``removed_byte_count = 0`` and ``removed_tail_length = 0``.
       Kind = ``"terminator_restored"``. The valid event is NEVER
       deleted.

    Any newline-terminated malformed record is genuine corruption
    and is rejected — this helper does not touch the file in that
    case.

    Returns ``None`` when the log is already well-formed.

    ONLY safe to call while the writer lock is held.
    """
    if not events_path.exists():
        return None
    raw = events_path.read_bytes()
    if not raw:
        return None

    # Newline-terminated file: real corruption ⇒ fail closed.
    if raw.endswith(b"\n"):
        text = raw.decode("utf-8", errors="strict")
        for i, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ShadowError(
                    f"events.jsonl line {i + 1} is malformed and the file "
                    f"is newline-terminated — this is not an interrupted "
                    f"append and cannot be repaired ({exc.msg})"
                ) from exc
        return None

    text = raw.decode("utf-8", errors="strict")
    lines = text.splitlines(keepends=False)
    # Every prior line must be valid JSON — a malformed middle line
    # is a real corruption and fails closed.
    prev_event_id = ""
    for i, line in enumerate(lines[:-1]):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            raise ShadowError(
                f"events.jsonl line {i + 1} is malformed (middle line) — "
                f"tail repair aborted. Manual investigation required."
            )
        if isinstance(obj, dict) and "event_id" in obj:
            prev_event_id = obj["event_id"]
    if not lines:
        return None
    last = lines[-1].strip()
    if not last:
        return None

    pre_repair_file_sha256 = hashlib.sha256(raw).hexdigest()

    try:
        last_obj = json.loads(last)
    except json.JSONDecodeError:
        # Case 1: unterminated MALFORMED tail. Remove it.
        good = "\n".join(lines[:-1])
        if good:
            good += "\n"
        good_bytes = good.encode("utf-8")
        removed_bytes = len(raw) - len(good_bytes)
        removed_tail_bytes = raw[len(good_bytes):]
        tail_sha256 = hashlib.sha256(removed_tail_bytes).hexdigest()
        tmp = events_path.with_suffix(events_path.suffix + ".tmp")
        tmp.write_text(good, encoding="utf-8")
        os.replace(str(tmp), str(events_path))
        return {
            "kind": "tail_removed",
            "removed_byte_count": removed_bytes,
            "removed_tail_length": len(last),
            "removed_tail_sha256": tail_sha256,
            "previous_event_id": prev_event_id,
            "pre_repair_file_sha256": pre_repair_file_sha256,
        }

    # Case 2: unterminated VALID tail. Only the explicit
    # --repair-event-log-tail command may normalize this — automatic
    # (run_cycle) repair leaves it alone so the subsequent event-log
    # load fails closed and the operator is forced to invoke repair
    # deliberately.
    if not allow_terminator_restore:
        return None
    final_event_id = ""
    if isinstance(last_obj, dict) and isinstance(last_obj.get("event_id"), str):
        final_event_id = last_obj["event_id"]
    with events_path.open("ab") as f:
        f.write(b"\n")
    return {
        "kind": "terminator_restored",
        "removed_byte_count": 0,
        "removed_tail_length": 0,
        "removed_tail_sha256": hashlib.sha256(b"").hexdigest(),
        "previous_event_id": final_event_id,
        "pre_repair_file_sha256": pre_repair_file_sha256,
    }


def _validate_event_log_readonly(
    events_path: Path,
    state: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    """Validate the event log for a read-only caller (status / report).

    * Existing log → routed through the canonical
      :func:`_validate_and_load_event_ids` validator (strict framing +
      full semantic validation). Any anomaly raises.
    * Missing log → acceptable only when the state proves no forward
      observations exist. Any candidate with processed forward bars
      or completed trades makes a missing event file an
      audit-integrity mismatch.

    Never mutates the file.
    """
    if events_path.exists():
        _validate_and_load_event_ids(events_path, manifest, require_terminated=True)
        return
    for cid, cs in state.get("candidates", {}).items():
        if cs.get("processed_forward_bar_count", 0) > 0:
            raise ShadowError(
                f"events.jsonl missing but candidate {cid!r} has processed "
                f"{cs['processed_forward_bar_count']} forward bars — "
                f"audit integrity mismatch"
            )
        if cs.get("completed_trade_count", 0) > 0:
            raise ShadowError(
                f"events.jsonl missing but candidate {cid!r} has "
                f"{cs['completed_trade_count']} completed trades — "
                f"audit integrity mismatch"
            )


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


_EXPERIMENT_CANDIDATE_ID = "__experiment__"

# Every event_type persisted to events.jsonl. The validator rejects
# anything outside this set.
_ALLOWED_EVENT_TYPES = frozenset({
    "BAR_PROCESSED",
    "BULLISH_CROSSOVER",
    "BEARISH_CROSSOVER",
    "SHADOW_BUY_SCHEDULED",
    "SHADOW_BUY_EXECUTED",
    "SHADOW_SELL_SCHEDULED",
    "SHADOW_SELL_EXECUTED",
    "ENTRY_FILTER_BLOCKED",
    "DUPLICATE_BAR_SKIPPED",
    "DATA_GAP_DETECTED",
    "EVENT_LOG_TAIL_RECOVERED",
    "EVENT_LOG_TERMINATOR_RESTORED",
})

_ALLOWED_POSITION_STATES = frozenset({"long", "flat"})
_NUMERIC_EVENT_FIELDS = (
    "short_sma", "long_sma", "price", "quantity", "cash", "equity",
)


def _validate_and_prepare_bars(
    bars: Sequence[Bar],
) -> tuple[list[Bar], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(validated_bars, duplicate_records, gap_records)``.

    * Non-finite OHLCV → :class:`ShadowError`.
    * Same timestamp + identical OHLCV → dedupe; each duplicate
      contributes a record ``{"timestamp": iso}``.
    * Same timestamp + different OHLCV → :class:`ShadowError`.
    * Intraday gaps within a regular session (gap > 65 minutes, < 12h,
      same UTC date) contribute a record — do not synthesize.
    """
    if not bars:
        return [], [], []
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
    duplicate_records: list[dict[str, Any]] = []
    for ts, b in entries:
        if dedup and dedup[-1][0] == ts:
            prev = dedup[-1][1]
            if (b.open, b.high, b.low, b.close, b.volume) != (
                prev.open, prev.high, prev.low, prev.close, prev.volume,
            ):
                raise ShadowError(
                    f"conflicting OHLCV for the same timestamp {ts.isoformat()}"
                )
            duplicate_records.append({"timestamp": ts.isoformat()})
            continue
        dedup.append((ts, b))

    gap_records: list[dict[str, Any]] = []
    for i in range(1, len(dedup)):
        prev_ts, _ = dedup[i - 1]
        cur_ts, _ = dedup[i]
        seconds = (cur_ts - prev_ts).total_seconds()
        if (
            prev_ts.date() == cur_ts.date()
            and 65 * 60 < seconds < 12 * 3600
        ):
            gap_records.append({
                "prev_bar_utc": prev_ts.isoformat(),
                "next_bar_utc": cur_ts.isoformat(),
                "gap_seconds": seconds,
            })
    return [b for _, b in dedup], duplicate_records, gap_records


def _make_experiment_event(
    event_type: str,
    *,
    signal_bar_utc: str,
    manifest_hash_str: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic experiment-scoped audit event.

    Uses the reserved candidate ID ``__experiment__``. Deterministic
    ID derived from experiment_id + event_type + signal_bar +
    detail-serialized payload so re-running the same input never
    yields a duplicate entry.
    """
    detail_json = json.dumps(detail or {}, sort_keys=True, default=str)
    return {
        "event_id": _event_id(
            EXPERIMENT_ID, _EXPERIMENT_CANDIDATE_ID, event_type,
            signal_bar_utc, detail_json,
        ),
        "experiment_id": EXPERIMENT_ID,
        "candidate_id": _EXPERIMENT_CANDIDATE_ID,
        "event_type": event_type,
        "signal_bar_utc": signal_bar_utc,
        "execution_bar_utc": None,
        "event_timestamp_utc": signal_bar_utc,
        "reason": None,
        "short_sma": None,
        "long_sma": None,
        "filter": None,
        "filter_result": None,
        "price": None,
        "quantity": None,
        "cash": None,
        "equity": None,
        "position_state": None,
        "manifest_hash": manifest_hash_str,
        "detail": detail or {},
    }


def _make_recovery_event(
    *,
    manifest_hash_str: str,
    recovered_detail: dict[str, Any],
    now_utc: datetime,
) -> dict[str, Any]:
    """Build the recovery audit event (either type).

    The ID is derived deterministically from
    ``(manifest_hash, event_type, pre_repair_file_sha256,
    removed_tail_sha256, previous_event_id)``. ``now_utc`` fills
    the human-visible timestamps but is deliberately excluded from
    the ID so repeated repairs of the same content produce
    identical event IDs.
    """
    kind = recovered_detail.get("kind", "tail_removed")
    event_type = (
        "EVENT_LOG_TERMINATOR_RESTORED" if kind == "terminator_restored"
        else "EVENT_LOG_TAIL_RECOVERED"
    )
    tail_sha = recovered_detail.get("removed_tail_sha256", "")
    prev_id = recovered_detail.get("previous_event_id", "")
    pre_sha = recovered_detail.get("pre_repair_file_sha256", "")
    event_id = _event_id(
        manifest_hash_str, event_type, pre_sha, tail_sha, prev_id,
    )
    ts = now_utc.astimezone(timezone.utc).isoformat()
    return {
        "event_id": event_id,
        "experiment_id": EXPERIMENT_ID,
        "candidate_id": _EXPERIMENT_CANDIDATE_ID,
        "event_type": event_type,
        "signal_bar_utc": ts,
        "execution_bar_utc": None,
        "event_timestamp_utc": ts,
        "reason": None,
        "short_sma": None,
        "long_sma": None,
        "filter": None,
        "filter_result": None,
        "price": None,
        "quantity": None,
        "cash": None,
        "equity": None,
        "position_state": None,
        "manifest_hash": manifest_hash_str,
        "detail": recovered_detail,
    }


def _build_experiment_events(
    duplicate_records: Sequence[dict[str, Any]],
    gap_records: Sequence[dict[str, Any]],
    manifest_hash_str: str,
    known_event_ids: set[str],
) -> list[dict[str, Any]]:
    """Build DUPLICATE_BAR_SKIPPED / DATA_GAP_DETECTED events, deduped
    against the existing event log so reruns do not append copies."""
    out: list[dict[str, Any]] = []
    # Merge consecutive duplicates for the same timestamp into a
    # single event with a count — deterministic if inputs are.
    counts: dict[str, int] = {}
    for r in duplicate_records:
        counts[r["timestamp"]] = counts.get(r["timestamp"], 0) + 1
    for ts, count in sorted(counts.items()):
        ev = _make_experiment_event(
            "DUPLICATE_BAR_SKIPPED",
            signal_bar_utc=ts,
            manifest_hash_str=manifest_hash_str,
            detail={"duplicate_timestamp_utc": ts, "skipped_count": count},
        )
        if ev["event_id"] not in known_event_ids:
            known_event_ids.add(ev["event_id"])
            out.append(ev)
    for gap in gap_records:
        ev = _make_experiment_event(
            "DATA_GAP_DETECTED",
            signal_bar_utc=gap["prev_bar_utc"],
            manifest_hash_str=manifest_hash_str,
            detail=dict(gap),
        )
        if ev["event_id"] not in known_event_ids:
            known_event_ids.add(ev["event_id"])
            out.append(ev)
    return out


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
                    # Classify the entry — no silent fallback.
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
                        raise ShadowError(
                            f"{cid}: impossible entry-classification state "
                            f"at {bar_ts.isoformat()} — episode_type="
                            f"{ep_type!r} had_crossover_block={ep_blocked} "
                            f"bullish_cross={bullish_cross}. "
                            "This branch must never be reached; if it "
                            "does the bookkeeping is inconsistent."
                        )
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
    """Process all unseen bars for all five candidates.

    Write-path ordering (the S62 audit-integrity contract):

      1. acquire writer lock
      2. load and validate manifest
      3. load and validate state
      4. validate all COMPLETE event records semantically against
         the manifest
      5. handle only the specifically permitted malformed
         unterminated tail (a semantically valid but unterminated
         final record is left untouched and fails closed — only the
         explicit ``--repair-event-log-tail`` command may normalize
         it)
      6. revalidate the complete (possibly repaired) log
         semantically
      7. process bars
      8. append events
      9. revalidate the resulting event log
      10. atomically write state

    A wrong experiment ID, manifest hash, candidate ID, event type,
    duplicate ID, invalid timestamp, or invalid field type raises
    :class:`ShadowError` at step 4 or 6/9 — always before state
    mutation, event append, tail deletion, or terminator restoration.
    """
    if dry_run:
        # Dry-run requires an initialized experiment — it must not
        # create the state directory, manifest, state, events, or a
        # persistent lock file. It may only read.
        manifest = load_manifest_readonly(state_dir)
        cutoff = _parse_ts(manifest["forward_cutoff_utc"])
        validated, duplicates_meta, gap_events = _validate_and_prepare_bars(bars)
        manifest_hash_str = manifest["candidate_manifest_sha256"]

        state_path = state_dir / _STATE_FILENAME
        if state_path.exists():
            state = load_state_readonly(state_dir, manifest)
        else:
            state = _init_state(manifest)
        events_path = state_dir / _EVENTS_FILENAME
        # Read-only in dry-run: strict framing + full semantic
        # validation; no repair, no writes. Any corruption — bad
        # framing, wrong experiment/manifest hash, unknown
        # candidate/event type, bad timestamp, non-finite field —
        # surfaces as ShadowError.
        known_event_ids = _validate_and_load_event_ids(
            events_path, manifest, require_terminated=True,
        )
        summary = _run_candidates(
            state, manifest, validated, cutoff, known_event_ids,
        )
        candidate_events = summary.pop("_events", [])
        experiment_events = _build_experiment_events(
            duplicates_meta, gap_events, manifest_hash_str, known_event_ids,
        )
        summary["dry_run"] = True
        summary["duplicate_bar_skipped_count"] = len(duplicates_meta)
        summary["duplicate_bar_events"] = duplicates_meta
        summary["gap_events"] = gap_events
        summary["experiment_events_would_persist"] = experiment_events
        summary["event_log_tail_recovered"] = None
        summary["candidate_events_would_append"] = len(candidate_events)
        summary["experiment_events_would_append"] = len(experiment_events)
        summary["events_would_append"] = (
            len(candidate_events) + len(experiment_events)
        )
        return summary

    # --- Write path ---------------------------------------------------
    lock_path = state_dir / _LOCK_FILENAME
    with _FileLock(lock_path):
        # Step 2: load and validate manifest.
        manifest = load_or_init_manifest(state_dir, now_utc)
        cutoff = _parse_ts(manifest["forward_cutoff_utc"])
        manifest_hash_str = manifest["candidate_manifest_sha256"]

        # Bar validation touches no shared file state; safe to run
        # here so a bad-bar error surfaces before state/events I/O.
        validated, duplicates_meta, gap_events = _validate_and_prepare_bars(bars)

        # Step 3: load and validate state. An invalid state must
        # never trigger event-log mutation.
        state = load_or_init_state(state_dir, manifest)

        events_path = state_dir / _EVENTS_FILENAME

        # Step 4: validate all COMPLETE event records semantically.
        # require_terminated=False tolerates a missing trailing
        # newline so step 5 can inspect the specific permitted
        # malformed-tail case, but every complete line — and any
        # final fragment that DOES parse as JSON — is still fully
        # validated here. A wrong experiment ID, manifest hash,
        # candidate ID, event type, duplicate ID, bad timestamp, or
        # invalid field type raises now, before any mutation.
        _validate_and_load_event_ids(
            events_path, manifest, require_terminated=False,
        )

        # Step 5: handle only the specifically permitted malformed
        # unterminated tail. allow_terminator_restore=False means a
        # semantically-valid-but-unterminated final record is left
        # completely untouched by this call.
        recovered = _repair_event_log_tail(
            events_path, allow_terminator_restore=False,
        )
        if events_path.exists():
            raw_after = events_path.read_bytes()
            if raw_after and not raw_after.endswith(b"\n"):
                # The only remaining reason the file can still be
                # unterminated here is a semantically valid final
                # record that this automatic path is not permitted
                # to normalize.
                raise ShadowError(
                    "events.jsonl ends with a valid but unterminated "
                    "(missing trailing newline) final record — normal "
                    "processing cannot repair this automatically. Run "
                    "--repair-event-log-tail."
                )

        # Step 6: revalidate the complete (possibly repaired) log —
        # this becomes the authoritative known-ID set for dedup.
        known_event_ids = _validate_and_load_event_ids(
            events_path, manifest, require_terminated=True,
        )

        # Step 7: process bars.
        summary = _run_candidates(
            state, manifest, validated, cutoff, known_event_ids,
        )
        collected = summary.pop("_events")

        experiment_events = _build_experiment_events(
            duplicates_meta, gap_events, manifest_hash_str, known_event_ids,
        )
        if recovered is not None:
            recovery_event = _make_recovery_event(
                manifest_hash_str=manifest_hash_str,
                recovered_detail=recovered,
                now_utc=now_utc,
            )
            if recovery_event["event_id"] not in known_event_ids:
                known_event_ids.add(recovery_event["event_id"])
                experiment_events.insert(0, recovery_event)

        # Step 8: append events.
        all_events = experiment_events + collected
        _append_events(events_path, all_events)

        # Step 9: revalidate the resulting event log.
        _validate_and_load_event_ids(
            events_path, manifest, require_terminated=True,
        )

        # Step 10: atomically write state.
        _atomic_write_json(state_dir / _STATE_FILENAME, state)

    candidate_events_appended = len(collected)
    experiment_events_appended = len(experiment_events)
    summary["dry_run"] = False
    summary["duplicate_bar_skipped_count"] = len(duplicates_meta)
    summary["duplicate_bar_events"] = duplicates_meta
    summary["gap_events"] = gap_events
    summary["experiment_events_persisted"] = experiment_events
    summary["event_log_tail_recovered"] = recovered
    summary["candidate_events_appended"] = candidate_events_appended
    summary["experiment_events_appended"] = experiment_events_appended
    summary["events_appended"] = (
        candidate_events_appended + experiment_events_appended
    )
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
    p.add_argument("--repair-event-log-tail", action="store_true",
                   help="Explicit repair mode: acquire the writer lock, "
                        "validate manifest + state, physically truncate a "
                        "malformed unterminated final line (only), emit a "
                        "deterministic EVENT_LOG_TAIL_RECOVERED audit "
                        "event, and exit. Rejects newline-terminated "
                        "malformed final records and any malformed middle "
                        "line. Does not process any strategy bars.")
    return p


def repair_event_log_tail_command(
    state_dir: Path, *, now_utc: datetime,
) -> dict[str, Any]:
    """Explicit repair mode used by ``--repair-event-log-tail``.

    Ordering:

      1. require manifest and state; validate state
      2. inspect the event log without mutating it
      3. semantically validate every complete prefix event against
         the manifest — a final unterminated record that DOES parse
         as JSON is validated too (and rejected without mutation if
         semantically invalid); a final fragment that fails to parse
         is excluded from this check (that is the malformed-tail
         case handled by the physical repair step)
      4. perform the repair (tail removal or terminator restoration)
      5. semantically revalidate the repaired log
      6. append the deterministic recovery event
      7. semantically revalidate the final log

    Any failure at step 1 or 3 leaves the event-log bytes completely
    unchanged — the physical repair (step 4) only runs after
    validation has succeeded.
    """
    manifest = load_manifest_readonly(state_dir)
    # State MUST exist and validate. A missing state file is not a
    # recoverable state for the tail-repair command.
    state_path = state_dir / _STATE_FILENAME
    if not state_path.exists():
        raise ShadowError(
            f"no shadow state at {state_path} — --repair-event-log-tail "
            "requires an initialized experiment. Run --once first."
        )
    manifest_hash_str = manifest["candidate_manifest_sha256"]
    with _FileLock(state_dir / _LOCK_FILENAME):
        load_state_readonly(state_dir, manifest)
        events_path = state_dir / _EVENTS_FILENAME

        # Steps 2-3: inspect + semantically validate every complete
        # prefix event AND the final fragment if it parses as JSON —
        # all without mutating the file. Any anomaly raises here,
        # before the physical repair ever runs.
        _validate_and_load_event_ids(
            events_path, manifest, require_terminated=False,
        )

        # Step 4: perform the physical repair (only reached once
        # validation above has succeeded).
        recovered = _repair_event_log_tail(
            events_path, allow_terminator_restore=True,
        )
        recovery_event: dict[str, Any] | None = None
        if recovered is not None:
            # Step 5: semantically revalidate the repaired log.
            known = _validate_and_load_event_ids(
                events_path, manifest, require_terminated=True,
            )
            # Step 6: append the deterministic recovery event.
            recovery_event = _make_recovery_event(
                manifest_hash_str=manifest_hash_str,
                recovered_detail=recovered,
                now_utc=now_utc,
            )
            if recovery_event["event_id"] not in known:
                _append_events(events_path, [recovery_event])
            # Step 7: semantically revalidate the final log.
            _validate_and_load_event_ids(
                events_path, manifest, require_terminated=True,
            )
    return {
        "experiment_id": manifest["experiment_id"],
        "manifest_hash": manifest_hash_str,
        "repaired": recovered is not None,
        "recovered_detail": recovered,
        "recovery_event": recovery_event,
        "research_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    state_dir = Path(args.state_dir)
    now_utc = datetime.now(timezone.utc)
    try:
        if args.repair_event_log_tail:
            summary = repair_event_log_tail_command(state_dir, now_utc=now_utc)
            print(json.dumps(summary, indent=2, default=str))
            return 0

        if args.status:
            manifest = load_manifest_readonly(state_dir)
            state = load_state_readonly(state_dir, manifest)
            # Read-only event-log validation — status must fail closed
            # on corruption without repairing.
            _validate_event_log_readonly(
                state_dir / _EVENTS_FILENAME, state, manifest,
            )
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
