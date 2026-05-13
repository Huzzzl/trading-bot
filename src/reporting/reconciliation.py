"""
reporting/reconciliation.py
---------------------------
Standalone order reconciliation logic.

Shared by :class:`~src.reporting.report_generator.ReportGenerator` and the
offline replay utility :mod:`src.tools.replay_order_reconciliation`.

The inputs are duck-typed: any object with ``client_order_id``, ``symbol``,
``side``, ``quantity``, and ``reason`` attributes works for *intents*, and any
object that additionally has a ``status`` attribute works for *results*.
"""

from __future__ import annotations

from typing import Any


def build_reconciliation(intents: list, results: list) -> dict[str, Any]:
    """Compare order intents with order results and return a summary dict.

    When all intents and results carry a ``client_order_id``, matching is
    done by ID (order-independent).  Otherwise falls back to positional
    comparison.  Sets ``missing_ids_warn=True`` when intents have IDs but
    results don't, which promotes ``overall_status`` to ``"WARN"``.

    Parameters
    ----------
    intents:
        Objects with ``client_order_id``, ``symbol``, ``side``, ``quantity``,
        ``reason`` attributes.
    results:
        Objects with all intent attributes plus ``status``.

    Returns
    -------
    dict
        Keys: ``intent_count``, ``result_count``, ``unmatched_count``,
        ``rejected_count``, ``accepted_count``, ``filled_count``,
        ``mismatch_count``, ``missing_ids_warn``,
        ``overall_status`` (``"PASS"`` or ``"WARN"``).
    """
    results = list(results)  # accept any iterable

    intent_count   = len(intents)
    result_count   = len(results)
    rejected_count = sum(1 for r in results if r.status == "rejected")
    accepted_count = sum(1 for r in results if r.status == "accepted")
    filled_count   = sum(1 for r in results if r.status == "filled")

    intent_ids = [oi.client_order_id for oi in intents]
    result_ids = [r.client_order_id  for r  in results]

    all_intents_have_ids = bool(intents) and all(cid is not None for cid in intent_ids)
    all_results_have_ids = bool(results) and all(cid is not None for cid in result_ids)
    missing_ids_warn = bool(all_intents_have_ids and not all_results_have_ids)

    mismatch_count  = 0
    unmatched_count = 0

    if all_intents_have_ids and all_results_have_ids:
        intent_map = {oi.client_order_id: oi for oi in intents}
        result_map = {r.client_order_id:  r  for r  in results}
        all_ids = set(intent_map) | set(result_map)
        unmatched_count = len(all_ids) - len(set(intent_map) & set(result_map))
        for cid in set(intent_map) & set(result_map):
            oi = intent_map[cid]
            r  = result_map[cid]
            if (oi.symbol   != r.symbol   or
                oi.side     != r.side     or
                oi.quantity != r.quantity or
                oi.reason   != r.reason):
                mismatch_count += 1
    else:
        unmatched_count = abs(intent_count - result_count)
        for oi, r in zip(intents, results):
            if (oi.symbol   != r.symbol   or
                oi.side     != r.side     or
                oi.quantity != r.quantity or
                oi.reason   != r.reason):
                mismatch_count += 1

    warn = (
        missing_ids_warn
        or result_count != intent_count
        or unmatched_count > 0
        or mismatch_count > 0
    )
    return {
        "intent_count":    intent_count,
        "result_count":    result_count,
        "unmatched_count": unmatched_count,
        "rejected_count":  rejected_count,
        "accepted_count":  accepted_count,
        "filled_count":    filled_count,
        "mismatch_count":  mismatch_count,
        "missing_ids_warn": missing_ids_warn,
        "overall_status":  "WARN" if warn else "PASS",
    }
