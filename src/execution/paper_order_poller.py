"""
execution/paper_order_poller.py
--------------------------------
Read-only post-submit status polling for paper orders.

After submit_order returns an OrderResult, main.py can optionally call
poll_order_status() to wait until the order reaches a terminal or definitive
state.  The polling loop only calls get_order_by_id — it never calls
submit_order, cancel_order, or any other mutating endpoint.

What this module never does
---------------------------
* Never calls submit_order.
* Never calls cancel_order.
* Never modifies the ledger directly (main.py does that after polling).
* Never reads or writes config.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger

_logger = get_logger(__name__)

# Terminal statuses (normalized).  Once reached, polling stops.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"filled", "cancelled", "rejected"})

# Fail statuses (normalized).  Reaching one raises RuntimeError.
_FAIL_STATUSES: frozenset[str] = frozenset({"cancelled", "rejected"})


# ---------------------------------------------------------------------------
# Status helpers (mirrors AlpacaBrokerAdapter without the ValueError)
# ---------------------------------------------------------------------------

def _normalize_status(raw: Any) -> str:
    """Map an Alpaca status (str or SDK enum) to a canonical string.

    Unlike the broker's version this never raises — unknown statuses pass
    through lowercased so the poller can still record and continue.
    """
    s = str(raw).strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    s = s.lower()
    if s in {"new", "pending_new", "accepted", "accepted_for_bidding"}:
        return "accepted"
    if s in {"filled", "partially_filled"}:
        return "filled"
    if s in {"canceled", "cancelled", "expired", "replaced"}:
        return "cancelled"
    if s in {"rejected", "stopped", "suspended", "calculated"}:
        return "rejected"
    return s


def _is_terminal(status: str) -> bool:
    return status in _TERMINAL_STATUSES


def _response_get(response: Any, key: str, default: Any = None) -> Any:
    if isinstance(response, dict):
        return response.get(key, default)
    return getattr(response, key, default)


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------

def _write_poll_artifact(output_dir: Path, poll_result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "paper_order_status_poll.json"
    with open(artifact_path, "w", encoding="utf-8") as fh:
        json.dump(poll_result, fh, indent=2)
    _logger.info("Poll artifact written → %s", artifact_path)


# ---------------------------------------------------------------------------
# Main polling function
# ---------------------------------------------------------------------------

def poll_order_status(
    client: Any,
    alpaca_order_id: str,
    client_order_id: str,
    initial_status: str,
    timeout_seconds: int,
    interval_seconds: float,
    output_dir: Path,
    *,
    _sleep_fn: Any = None,
    _monotonic_fn: Any = None,
) -> dict[str, Any]:
    """Poll Alpaca read-only until the order is terminal or the timeout expires.

    Parameters
    ----------
    client:
        A ``TradingClient`` (real SDK) or test double that exposes
        ``get_order_by_id``.  ``submit_order`` and ``cancel_order`` are
        never called.
    alpaca_order_id:
        The Alpaca-assigned UUID for the order.
    client_order_id:
        The local ``client_order_id`` (for artifact and log messages).
    initial_status:
        The normalised status from the ``OrderResult`` returned by
        ``submit_order``.
    timeout_seconds:
        Maximum wall-clock seconds to wait.  After expiry polling stops.
    interval_seconds:
        Seconds to sleep between polls.
    output_dir:
        Directory where ``paper_order_status_poll.json`` is written.
    _sleep_fn / _monotonic_fn:
        Injected time functions for unit tests.  Production code uses
        ``time.sleep`` and ``time.monotonic``.

    Returns
    -------
    dict
        Poll result with keys: ``client_order_id``, ``alpaca_order_id``,
        ``initial_status``, ``final_status``, ``poll_count``,
        ``timeout_seconds``, ``timed_out``, ``checked_at_utc``,
        ``raw_statuses``.

    Raises
    ------
    RuntimeError
        If ``final_status`` is in ``{"cancelled", "rejected"}`` — always
        written to artifact first.  Also raised if the order times out and
        the final observed status is a fail status.
    """
    _sleep   = _sleep_fn    or time.sleep
    _mono    = _monotonic_fn or time.monotonic

    checked_at   = datetime.now(timezone.utc).isoformat()
    deadline     = _mono() + timeout_seconds
    current      = initial_status
    raw_statuses = [initial_status]
    poll_count   = 0
    timed_out    = False

    while not _is_terminal(current):
        if _mono() >= deadline:
            timed_out = True
            break
        _sleep(interval_seconds)
        poll_count += 1
        try:
            response   = client.get_order_by_id(alpaca_order_id)
            raw        = _response_get(response, "status", "")
            current    = _normalize_status(raw)
            raw_statuses.append(current)
        except Exception as exc:
            raw_statuses.append(f"error:{exc}")
            _logger.warning("Poll lookup error for %r: %s", client_order_id, exc)

    final_status = current

    poll_result: dict[str, Any] = {
        "client_order_id": client_order_id,
        "alpaca_order_id": alpaca_order_id,
        "initial_status":  initial_status,
        "final_status":    final_status,
        "poll_count":      poll_count,
        "timeout_seconds": timeout_seconds,
        "timed_out":       timed_out,
        "checked_at_utc":  checked_at,
        "raw_statuses":    raw_statuses,
    }

    # Always write artifact before raising.
    _write_poll_artifact(output_dir, poll_result)

    if final_status in _FAIL_STATUSES:
        raise RuntimeError(
            f"Paper order {client_order_id!r} reached terminal status "
            f"{final_status!r} after {poll_count} poll(s). "
            "No order was cancelled. See paper_order_status_poll.json for details."
        )

    if timed_out:
        _logger.warning(
            "Paper order polling timed out after %ds for %r. "
            "Final observed status: %r. Order may still be processing. "
            "No order was cancelled.",
            timeout_seconds,
            client_order_id,
            final_status,
        )

    _logger.info(
        "Paper order polling complete: %r initial=%r final=%r polls=%d timed_out=%s",
        client_order_id,
        initial_status,
        final_status,
        poll_count,
        timed_out,
    )

    return poll_result
