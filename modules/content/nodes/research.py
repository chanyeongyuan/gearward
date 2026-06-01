"""
Content node: Research — trending signals + competitor positioning.

Tier-0 (Gemini Flash): high-volume, cheap extraction.
Writes research summary to MemoryService for downstream nodes and future runs.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import litellm

if TYPE_CHECKING:
    from modules.content.graph import ContentState

_SYSTEM = """\
You are a viral content researcher. Given a brand profile and product brief,
identify: (1) trending hooks in this category right now, (2) competitor weak spots
worth exploiting, (3) audience pain points this product uniquely solves.
Return compact JSON only — no prose outside the JSON object.
"""

_USER = """\
Brand: {brand}
Product: {product}

Return JSON:
{{
  "trending_hooks": ["hook1", "hook2", "hook3"],
  "competitor_gaps": ["gap1", "gap2"],
  "audience_pain_points": ["pain1", "pain2"],
  "category_insight": "one sentence"
}}
"""


def run_research(state: "ContentState") -> "ContentState":
    harness = state["__harness__"]
    ctx = state["__context__"]
    brand = state.get("brand_profile", {})
    product = state.get("product_brief", {})

    # Check memory for cached research (saves a model call if recent)
    from core import MemoryRecord, Module
    cached = harness.memory.read(ctx.client_id, Module.CONTENT, "trending research", k=1)
    if cached and (cached[0].score or 0) > 0.85:
        research = json.loads(cached[0].content)
        return {**state, "research": research}

    response = litellm.completion(
        model="tier-0",
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _USER.format(
                brand=json.dumps(brand, indent=2),
                product=json.dumps(product, indent=2),
            )},
        ],
        base_url=harness._litellm_base_url,
        api_key=harness._litellm_api_key,
        response_format={"type": "json_object"},
    )

    research = json.loads(response.choices[0].message.content)

    # Persist to memory so the next run starts with context
    harness.memory.write(MemoryRecord(
        client_id=ctx.client_id,
        module=Module.CONTENT,
        content=json.dumps(research),
        metadata={"node": "research", "brand": brand.get("name", "")},
    ))

    cost = getattr(response, "usage", None)
    token_cost = state.get("__token_cost__", 0.0)
    if cost:
        token_cost += (cost.prompt_tokens * 0.075 + cost.completion_tokens * 0.3) / 1_000_000

    return {**state, "research": research, "__token_cost__": token_cost, "__tier_used__": 0}
