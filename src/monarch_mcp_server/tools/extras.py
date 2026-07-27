"""
Extras: propose/apply pattern for Monarch write operations.

Adds two tools layered on top of robcerda's (otherwise read-only) MCP:
  - propose_categorization: stage a batch of category assignments (no writes)
  - apply_categorization:   commit a previously-proposed batch (single approval)

Why this layer:
  claude.ai's connector UI prompts for approval per write-tool call. Without
  batching, categorizing 30 transactions = 30 approval taps. The propose step
  is a pure read; only apply mutates Monarch, so one approval covers the batch.

State:
  In-memory dict, single-process, single-user. 10-minute TTL. On service
  restart, in-flight batches are dropped — re-propose if that happens.

Deployment target on droplet:
  ~/monarch-mcp-server/src/monarch_mcp_server/tools/extras.py
And add `extras` to the imports in tools/__init__.py.
"""

import asyncio
import logging
import secrets
import time
from typing import Any

from monarch_mcp_server.app import mcp
from monarch_mcp_server.client import get_monarch_client
from monarch_mcp_server.helpers import json_error, json_success

logger = logging.getLogger(__name__)

_BATCH_TTL_SECONDS = 600
_batches: dict[str, dict[str, Any]] = {}
_batches_lock = asyncio.Lock()


def _evict_expired(now: float) -> None:
    expired = [bid for bid, b in _batches.items() if b["expires_at"] <= now]
    for bid in expired:
        del _batches[bid]


@mcp.tool()
async def propose_categorization(proposals: list[dict]) -> str:
    """
    Stage a batch of category assignments. PURE READ — no writes happen here.

    Workflow: fetch needs-review transactions, decide which category each
    should get based on Aaron's guidance in the conversation, then call this
    tool with the full list. Show Aaron the returned proposals so he can
    confirm, then call apply_categorization(batch_id) to commit.

    Args:
        proposals: List of dicts. Each dict requires:
            - transaction_id (str): Monarch transaction ID
            - category_id (str):    Monarch category ID to assign
          Optional:
            - reason (str):         short human-readable justification, echoed
                                    back so Aaron can sanity-check before apply
                                    (e.g. "Trader Joe's -> Groceries per rule")
            - merchant (str):       merchant name for display
            - amount (str|number):  amount for display

    Returns:
        JSON {batch_id, expires_at_unix, ttl_seconds, count, proposals}.
        Batch expires in 10 minutes.
    """
    try:
        if not isinstance(proposals, list) or not proposals:
            return json_error(
                "propose_categorization",
                ValueError("proposals must be a non-empty list"),
            )

        for i, p in enumerate(proposals):
            if not isinstance(p, dict):
                return json_error(
                    "propose_categorization",
                    ValueError(f"proposals[{i}] must be a dict"),
                )
            if "transaction_id" not in p or "category_id" not in p:
                return json_error(
                    "propose_categorization",
                    ValueError(
                        f"proposals[{i}] missing transaction_id or category_id"
                    ),
                )

        batch_id = secrets.token_urlsafe(12)
        now = time.time()
        expires_at = now + _BATCH_TTL_SECONDS

        async with _batches_lock:
            _evict_expired(now)
            _batches[batch_id] = {
                "proposals": list(proposals),
                "created_at": now,
                "expires_at": expires_at,
            }

        return json_success(
            {
                "batch_id": batch_id,
                "expires_at_unix": expires_at,
                "ttl_seconds": _BATCH_TTL_SECONDS,
                "count": len(proposals),
                "proposals": proposals,
            }
        )
    except Exception as e:
        return json_error("propose_categorization", e)


@mcp.tool()
async def apply_categorization(
    batch_id: str,
    mark_reviewed: bool = True,
) -> str:
    """
    Commit a previously-proposed categorization batch to Monarch.

    WRITE OPERATION — claude.ai will prompt Aaron for approval before this runs.
    One approval covers the entire batch.

    Args:
        batch_id: ID returned by propose_categorization.
        mark_reviewed: Also clear needs_review on each transaction
                       (default True — typical "categorize then mark reviewed").

    Returns:
        JSON per-transaction success/failure summary. Failed rows include the
        error message so Aaron can re-propose just the failures if needed.
    """
    try:
        async with _batches_lock:
            now = time.time()
            _evict_expired(now)
            batch = _batches.pop(batch_id, None)

        if batch is None:
            return json_error(
                "apply_categorization",
                ValueError(
                    f"batch_id {batch_id!r} not found or expired. "
                    "Re-run propose_categorization."
                ),
            )

        proposals = batch["proposals"]
        client = await get_monarch_client()

        async def _apply_one(p: dict) -> None:
            params: dict[str, Any] = {
                "transaction_id": p["transaction_id"],
                "category_id": p["category_id"],
            }
            if mark_reviewed:
                params["needs_review"] = False
            await client.update_transaction(**params)

        tasks = [_apply_one(p) for p in proposals]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        results: dict[str, Any] = {
            "batch_id": batch_id,
            "total": len(proposals),
            "successful": 0,
            "failed": 0,
            "mark_reviewed": mark_reviewed,
            "errors": [],
        }

        for p, outcome in zip(proposals, outcomes):
            if isinstance(outcome, Exception):
                results["failed"] += 1
                results["errors"].append(
                    {
                        "transaction_id": p["transaction_id"],
                        "category_id": p["category_id"],
                        "error": str(outcome),
                    }
                )
            else:
                results["successful"] += 1

        return json_success(results)
    except Exception as e:
        return json_error("apply_categorization", e)
