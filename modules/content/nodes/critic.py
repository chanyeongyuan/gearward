"""
Content node: Critic — scores draft on 6 dimensions; injects fix_directive.

Tier-0 (Gemini Flash): cheap, runs on every draft.
Uses the LLMJudgeEvaluator from backends/ via the harness evaluator slot.
Falls back to a lightweight inline judge if no evaluator is attached.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import litellm

from core import CONTENT_DIMENSIONS

if TYPE_CHECKING:
    from modules.content.graph import ContentState

_INLINE_JUDGE_SYSTEM = """\
You are a harsh content critic. Score this content on the given dimensions.
Return ONLY JSON: {"dimension_name": score_1_to_10, ...}
"""


def run_critic(state: "ContentState") -> "ContentState":
    harness = state["__harness__"]
    draft = state.get("draft", {})

    if harness.evaluator is not None:
        from core import AgentResult, Tier
        mock_result = AgentResult(
            output=draft,
            run_id=state.get("__context__", {}).run_id or "draft",
            tier_used=Tier.TIER_2,
            token_cost=0.0,
        )
        eval_result = harness.evaluator.score_online(mock_result, CONTENT_DIMENSIONS)
    else:
        eval_result = _inline_judge(state, draft)

    return {**state, "eval_result": eval_result}


def _inline_judge(state: "ContentState", draft: dict) -> object:
    """Lightweight fallback judge when no Evaluator backend is wired."""
    from core import EvalResult
    harness = state["__harness__"]

    dim_descriptions = "\n".join(
        f"- {d.name} (floor {d.min_score}): {d.description}"
        for d in CONTENT_DIMENSIONS
    )
    response = litellm.completion(
        model="tier-0",
        messages=[
            {"role": "system", "content": _INLINE_JUDGE_SYSTEM},
            {"role": "user", "content": (
                f"Content:\n{json.dumps(draft, indent=2)}\n\n"
                f"Dimensions:\n{dim_descriptions}"
            )},
        ],
        base_url=harness._litellm_base_url,
        api_key=harness._litellm_api_key,
        response_format={"type": "json_object"},
    )

    raw = json.loads(response.choices[0].message.content)
    scores = {d.name: float(raw.get(d.name, 0)) for d in CONTENT_DIMENSIONS}

    fail_dims = [d for d in CONTENT_DIMENSIONS if scores.get(d.name, 0) < d.min_score]
    total_weight = sum(d.weight for d in CONTENT_DIMENSIONS)
    average = sum(scores[d.name] * d.weight for d in CONTENT_DIMENSIONS) / total_weight
    passed = not fail_dims and average >= 7.0

    fix_directive = None
    if fail_dims:
        fix_directive = "REGENERATE. Failed: " + ", ".join(
            f"{d.name}={scores[d.name]:.1f}" for d in fail_dims
        )

    return EvalResult(
        scores=scores,
        average=average,
        passed=passed,
        fail_reason=str([d.name for d in fail_dims]) if fail_dims else None,
        fix_directive=fix_directive,
    )
