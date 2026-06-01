"""
Content node: Generator — writes hook + copy + image/video brief.

Tier-2 (Claude Sonnet): main generation work.
Injects fix_directive from the critic on regeneration passes so the model
knows exactly what failed and why — targeted repair, not a cold restart.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import litellm

if TYPE_CHECKING:
    from modules.content.graph import ContentState

_SYSTEM = """\
You are an expert viral content writer. Write content that is unmistakably
specific to this brand — a competitor find-and-replace must not work.
The hook must create a 'wait, what?' moment in under 2 seconds.
Return JSON only.
"""

_USER = """\
Brand profile: {brand}
Product intelligence: {pi}
Research signals: {research}
{fix_block}
Return JSON:
{{
  "hook": "the opening line / visual concept (≤12 words)",
  "body_copy": "the full script or caption (match the brand voice exactly)",
  "image_brief": "visual direction for Gemini/Imagen",
  "video_brief": "shot-by-shot direction for Higgsfield (≤15 seconds)",
  "platform": "instagram_reel | tiktok | linkedin",
  "estimated_duration_seconds": 15
}}
"""

_FIX_BLOCK = """\
PREVIOUS ATTEMPT FAILED. Fix directive from the critic:
{fix_directive}

Do NOT repeat the same approach. Address each failing dimension explicitly.
"""


def run_generate(state: "ContentState") -> "ContentState":
    harness = state["__harness__"]
    ctx = state["__context__"]
    brand = state.get("brand_profile", {})
    pi = state.get("product_intelligence", {})
    research = state.get("research", {})

    eval_result = state.get("eval_result")
    fix_block = ""
    regen_count = state.get("regeneration_count", 0)

    if eval_result and not eval_result.passed and eval_result.fix_directive:
        fix_block = _FIX_BLOCK.format(fix_directive=eval_result.fix_directive)
        regen_count += 1

    response = litellm.completion(
        model="tier-2",
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _USER.format(
                brand=json.dumps(brand, indent=2),
                pi=json.dumps(pi, indent=2),
                research=json.dumps(research, indent=2),
                fix_block=fix_block,
            )},
        ],
        base_url=harness._litellm_base_url,
        api_key=harness._litellm_api_key,
        response_format={"type": "json_object"},
    )

    draft = json.loads(response.choices[0].message.content)

    cost = getattr(response, "usage", None)
    token_cost = state.get("__token_cost__", 0.0)
    if cost:
        token_cost += (cost.prompt_tokens * 3.0 + cost.completion_tokens * 15.0) / 1_000_000

    return {
        **state,
        "draft": draft,
        "regeneration_count": regen_count,
        "eval_result": None,  # reset so critic scores the new draft
        "__token_cost__": token_cost,
        "__tier_used__": 2,
    }
