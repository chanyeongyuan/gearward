"""
Content node: Product Intelligence — per-SKU enriched brief.

Tier-0/1: Loads from artifacts cache (Postgres) if valid; regenerates otherwise.
The cached artifact is the "compute once, reuse" lever — blueprint §6 Lever 4.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import litellm
import psycopg

if TYPE_CHECKING:
    from modules.content.graph import ContentState

_SYSTEM = """\
You are a product intelligence analyst. Synthesise research signals and the
product brief into a rich product intelligence document that content writers
can use to generate highly specific, non-substitutable content.
Return JSON only.
"""

_USER = """\
Product brief: {product}
Research signals: {research}

Return JSON:
{{
  "unique_mechanism": "what makes this product work differently",
  "proof_points": ["specific, quantifiable claim1", "claim2"],
  "ideal_customer_voice": "how the customer describes their pain, in their words",
  "hook_angles": ["specific angle tied to the mechanism", "another"],
  "non_substitutable_facts": ["fact that only this product can claim"]
}}
"""


def run_product_intelligence(state: "ContentState") -> "ContentState":
    harness = state["__harness__"]
    ctx = state["__context__"]
    product = state.get("product_brief", {})
    research = state.get("research", {})
    sku_key = product.get("sku_id", "global")

    # Try artifact cache first (Lever 4 — compute once, reuse)
    cached_pi = _load_cached_artifact(harness._db_url, ctx.client_id, sku_key)
    if cached_pi:
        return {**state, "product_intelligence": cached_pi}

    response = litellm.completion(
        model="tier-1",
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _USER.format(
                product=json.dumps(product, indent=2),
                research=json.dumps(research, indent=2),
            )},
        ],
        base_url=harness._litellm_base_url,
        api_key=harness._litellm_api_key,
        response_format={"type": "json_object"},
    )

    pi = json.loads(response.choices[0].message.content)

    # Cache the artifact — valid until the product brief changes
    _upsert_artifact(harness._db_url, ctx.client_id, sku_key, pi)

    cost = getattr(response, "usage", None)
    token_cost = state.get("__token_cost__", 0.0)
    if cost:
        token_cost += (cost.prompt_tokens * 0.8 + cost.completion_tokens * 4.0) / 1_000_000

    return {**state, "product_intelligence": pi, "__token_cost__": token_cost}


def _load_cached_artifact(db_url: str, client_id: str, key: str) -> dict | None:
    with psycopg.connect(db_url) as conn:
        row = conn.execute(
            """
            SELECT data FROM artifacts
            WHERE client_id = %s AND kind = 'product_intelligence' AND key = %s
              AND (valid_until IS NULL OR valid_until > now())
            """,
            (client_id, key),
        ).fetchone()
    return row[0] if row else None


def _upsert_artifact(db_url: str, client_id: str, key: str, data: dict) -> None:
    with psycopg.connect(db_url) as conn:
        conn.execute(
            """
            INSERT INTO artifacts (client_id, kind, key, data)
            VALUES (%s, 'product_intelligence', %s, %s)
            ON CONFLICT (client_id, kind, key)
            DO UPDATE SET data = EXCLUDED.data, created_at = now()
            """,
            (client_id, key, json.dumps(data)),
        )
