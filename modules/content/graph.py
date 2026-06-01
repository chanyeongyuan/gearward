"""
modules.content.graph — Content Engine LangGraph pipeline.

Nodes (blueprint §4.2):
  research             → pulls trending signals + competitor positioning
  product_intelligence → builds/updates the per-SKU product intel artifact
  generate             → writes hook + copy + image/video brief
  critic               → scores on 6 dimensions; injects fix_directive on fail
  novelty_check        → rejects generic / previously-seen concepts
  publish              → routes to prospect | client_channel | owned_channel

The critic loops back to generate up to MAX_REGENERATIONS times before
surfacing a human-review flag if it still can't clear the eval gate.
"""
from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from core import (
    CONTENT_DIMENSIONS,
    AgentRequest,
    ContentProvenance,
    DeploymentRouter,
    EvalResult,
    Module,
    Origin,
    RunContext,
    Tier,
)

if TYPE_CHECKING:
    from backends import LangGraphHarness

MAX_REGENERATIONS = 3
_router = DeploymentRouter()


# ── Graph state ───────────────────────────────────────────────────────────────

class ContentState(TypedDict, total=False):
    # Inputs
    client_id: str
    brand_profile: dict[str, Any]
    product_brief: dict[str, Any]
    provenance: ContentProvenance

    # Intermediate
    research: dict[str, Any]           # trending signals, hooks, competitors
    product_intelligence: dict[str, Any]  # per-SKU enriched brief
    draft: dict[str, Any]              # copy + image brief + video brief
    eval_result: EvalResult
    regeneration_count: int

    # Outputs
    published_artifact: dict[str, Any]
    requires_human_review: bool

    # Harness meta (set by LangGraphHarness before invoking)
    __harness__: Any
    __context__: RunContext
    __trace_id__: str
    __token_cost__: float
    __tier_used__: Tier


# ── Node implementations ──────────────────────────────────────────────────────

def research_node(state: ContentState) -> ContentState:
    from modules.content.nodes.research import run_research
    return run_research(state)


def product_intelligence_node(state: ContentState) -> ContentState:
    from modules.content.nodes.product_intelligence import run_product_intelligence
    return run_product_intelligence(state)


def generate_node(state: ContentState) -> ContentState:
    from modules.content.nodes.generator import run_generate
    return run_generate(state)


def critic_node(state: ContentState) -> ContentState:
    from modules.content.nodes.critic import run_critic
    return run_critic(state)


def novelty_check_node(state: ContentState) -> ContentState:
    from modules.content.nodes.novelty_check import run_novelty_check
    return run_novelty_check(state)


def publish_node(state: ContentState) -> ContentState:
    from modules.content.nodes.publisher import run_publish
    return run_publish(state)


# ── Conditional edges ─────────────────────────────────────────────────────────

def should_regenerate(state: ContentState) -> str:
    """After critic: loop back to generate, or continue to novelty check."""
    eval_result = state.get("eval_result")
    regen_count = state.get("regeneration_count", 0)

    if eval_result and not eval_result.passed:
        if regen_count < MAX_REGENERATIONS:
            return "generate"
        # Give up — flag for human review and continue to publish
        return "flag_and_continue"
    return "novelty_check"


def after_flag(state: ContentState) -> ContentState:
    return {**state, "requires_human_review": True}


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_content_graph(harness: "LangGraphHarness") -> Any:
    """
    Build and compile the content engine LangGraph.
    Call once at startup; register with: harness.register("content", graph)
    """
    graph = StateGraph(ContentState)

    graph.add_node("research", research_node)
    graph.add_node("product_intelligence", product_intelligence_node)
    graph.add_node("generate", generate_node)
    graph.add_node("critic", critic_node)
    graph.add_node("flag_and_continue", after_flag)
    graph.add_node("novelty_check", novelty_check_node)
    graph.add_node("publish", publish_node)

    graph.set_entry_point("research")

    graph.add_edge("research", "product_intelligence")
    graph.add_edge("product_intelligence", "generate")
    graph.add_edge("generate", "critic")
    graph.add_conditional_edges(
        "critic",
        should_regenerate,
        {
            "generate": "generate",
            "flag_and_continue": "flag_and_continue",
            "novelty_check": "novelty_check",
        },
    )
    graph.add_edge("flag_and_continue", "novelty_check")
    graph.add_edge("novelty_check", "publish")
    graph.add_edge("publish", END)

    return graph.compile()
