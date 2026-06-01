"""
jobs.refinement — "dreaming-lite" nightly memory refinement job (blueprint §5.2).

Runs on a schedule (e.g. nightly at 02:00 via Render Cron Job).
For each active client, uses Haiku (Tier-1) to:
  1. Cluster this client's recent memories by topic
  2. Distil repeated patterns into compact summaries
  3. Promote recurring insights; prune low-signal noise
  4. Write the distilled memories back, deleting the originals

This is the "dreaming-lite" pattern that delivers self-improving memory before
Anthropic's Dreaming feature becomes generally available. When Dreaming opens,
swap the implementation behind the MemoryService interface — zero refactor.

Usage:
    python -m jobs.refinement          # all clients
    python -m jobs.refinement CLIENT_ID  # single client (for testing)

Environment: DATABASE_URL, LITELLM_BASE_URL, LITELLM_MASTER_KEY
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

import litellm
import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_DB_URL = os.environ["DATABASE_URL"]
_LM_BASE = os.environ["LITELLM_BASE_URL"]
_LM_KEY = os.environ["LITELLM_MASTER_KEY"]

_DISTIL_SYSTEM = """\
You are a memory curator. Given a list of raw agent memories for one client,
do three things:
1. Group them by topic/theme.
2. Distil each group into one compact, high-signal summary (≤3 sentences).
3. Flag any memories to DELETE (duplicates, low signal, outdated).

Return JSON:
{
  "distilled": [
    {"topic": "...", "summary": "...", "source_ids": ["uuid1", "uuid2"]}
  ],
  "delete_ids": ["uuid3", "uuid4"]
}
"""


def fetch_active_clients(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute("SELECT id FROM clients").fetchall()
    return [str(row[0]) for row in rows]


def fetch_recent_memories(conn: psycopg.Connection, client_id: str, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, module, content, metadata, created_at
        FROM memory
        WHERE client_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (client_id, limit),
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "module": row[1],
            "content": row[2],
            "metadata": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
        }
        for row in rows
    ]


def refine_client(client_id: str) -> None:
    log.info("Refining memory for client %s", client_id)

    with psycopg.connect(_DB_URL) as conn:
        memories = fetch_recent_memories(conn, client_id)

    if len(memories) < 5:
        log.info("Client %s has <5 memories — skipping", client_id)
        return

    response = litellm.completion(
        model="tier-1",  # Haiku — pennies per client
        messages=[
            {"role": "system", "content": _DISTIL_SYSTEM},
            {"role": "user", "content": json.dumps(memories, indent=2)},
        ],
        base_url=_LM_BASE,
        api_key=_LM_KEY,
        response_format={"type": "json_object"},
    )

    result = json.loads(response.choices[0].message.content)
    distilled = result.get("distilled", [])
    delete_ids = result.get("delete_ids", [])

    with psycopg.connect(_DB_URL) as conn:
        # Write distilled summaries as new memory records
        for item in distilled:
            conn.execute(
                """
                INSERT INTO memory (client_id, module, content, metadata)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    client_id,
                    "content",  # cross-module distillation stored under content
                    item["summary"],
                    json.dumps({
                        "source": "refinement_job",
                        "topic": item["topic"],
                        "source_ids": item.get("source_ids", []),
                        "refined_at": datetime.now(timezone.utc).isoformat(),
                    }),
                ),
            )

        # Prune low-signal originals that were consolidated
        all_source_ids = [sid for item in distilled for sid in item.get("source_ids", [])]
        prune_ids = list(set(delete_ids + all_source_ids))

        if prune_ids:
            conn.execute(
                f"DELETE FROM memory WHERE id = ANY(%s) AND client_id = %s",
                (prune_ids, client_id),
            )

    log.info(
        "Client %s: %d distilled, %d pruned (from %d raw)",
        client_id,
        len(distilled),
        len(prune_ids) if prune_ids else 0,
        len(memories),
    )


def main(target_client: str | None = None) -> None:
    log.info("Refinement job starting — %s", datetime.now(timezone.utc).isoformat())

    with psycopg.connect(_DB_URL) as conn:
        clients = [target_client] if target_client else fetch_active_clients(conn)

    errors = 0
    for client_id in clients:
        try:
            refine_client(client_id)
        except Exception as exc:
            log.error("Failed to refine client %s: %s", client_id, exc)
            errors += 1

    log.info(
        "Refinement job complete. %d clients processed, %d errors.",
        len(clients),
        errors,
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    main(target)
