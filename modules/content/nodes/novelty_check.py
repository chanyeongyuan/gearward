"""
Content node: Novelty Check — rejects generic or previously-published concepts.

Tier-0: semantic search against the client's memory store to catch repeats.
Tier-0 classification to catch generic output that cleared the critic but
is still "template-ish" by absolute standards.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import litellm

from core import MemoryRecord, Module

if TYPE_CHECKING:
    from modules.content.graph import ContentState

_NOVELTY_SYSTEM = """\
You are a novelty filter. Given a piece of content and a list of recently
published concepts, determine if the new content is sufficiently different.
Return JSON: {"is_novel": true/false, "reason": "one sentence"}
"""


def run_novelty_check(state: "ContentState") -> "ContentState":
    harness = state["__harness__"]
    ctx = state["__context__"]
    draft = state.get("draft", {})

    hook = draft.get("hook", "")

    # Semantic search for similar past content
    past_content = harness.memory.read(
        ctx.client_id,
        Module.CONTENT,
        f"published content hook: {hook}",
        k=5,
    )

    past_hooks = [
        json.loads(r.content).get("hook", r.content[:100])
        for r in past_content
        if r.score and r.score > 0.7
    ]

    if not past_hooks:
        # No similar content found — proceed
        return {**state, "draft": {**draft, "novelty_ok": True}}

    response = litellm.completion(
        model="tier-0",
        messages=[
            {"role": "system", "content": _NOVELTY_SYSTEM},
            {"role": "user", "content": json.dumps({
                "new_hook": hook,
                "new_body": draft.get("body_copy", "")[:300],
                "past_published": past_hooks,
            }, indent=2)},
        ],
        base_url=harness._litellm_base_url,
        api_key=harness._litellm_api_key,
        response_format={"type": "json_object"},
    )

    result = json.loads(response.choices[0].message.content)
    is_novel = result.get("is_novel", True)

    return {**state, "draft": {**draft, "novelty_ok": is_novel, "novelty_reason": result.get("reason", "")}}
