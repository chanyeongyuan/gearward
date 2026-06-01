"""
modules.sales_training.graph — Sales Training LangGraph pipeline.

Flow: fetch_pipeline → analyze_pipeline → generate_playbook → generate_daily_guidance → deliver
Connects to CLIENT HubSpot (OAuth-linked portal stored in clients.hubspot_portal).
Self-hosted harness (analysis-heavy but low-volume, cost-sensitive).
"""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

import litellm
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from core import MemoryRecord, Module, RunContext

if TYPE_CHECKING:
    from backends import LangGraphHarness


class SalesTrainingState(TypedDict, total=False):
    client_id: str
    hubspot_portal: str
    hubspot_token: str        # per-client OAuth token from Postgres secrets store

    pipeline_data: dict       # deals + activities fetched from HubSpot
    analysis: dict            # bottlenecks, velocity, rep performance
    playbook: dict            # recommended actions per deal stage
    daily_guidance: list[dict]  # rep-level coaching cards for today

    __harness__: Any
    __context__: RunContext
    __trace_id__: str
    __token_cost__: float


# ── Nodes ─────────────────────────────────────────────────────────────────────

def fetch_pipeline(state: SalesTrainingState) -> SalesTrainingState:
    """Read deals + activities from the client's HubSpot portal via OAuth."""
    import httpx
    token = state.get("hubspot_token", "")
    portal = state.get("hubspot_portal", "")

    headers = {"Authorization": f"Bearer {token}"}
    base = "https://api.hubapi.com"

    with httpx.Client(headers=headers) as client:
        deals_resp = client.get(
            f"{base}/crm/v3/objects/deals",
            params={"limit": 100, "properties": "dealname,amount,dealstage,closedate,hubspot_owner_id"},
        )
        deals_resp.raise_for_status()
        deals = deals_resp.json().get("results", [])

        activities_resp = client.get(
            f"{base}/crm/v3/objects/engagements",
            params={"limit": 100, "properties": "engagement,associations"},
        )
        activities_resp.raise_for_status()
        activities = activities_resp.json().get("results", [])

    pipeline_data = {"deals": deals, "activities": activities, "portal": portal}
    return {**state, "pipeline_data": pipeline_data}


def analyze_pipeline(state: SalesTrainingState) -> SalesTrainingState:
    """Identify bottlenecks, stale deals, velocity trends. Tier-0."""
    harness = state["__harness__"]
    ctx = state["__context__"]
    pipeline = state.get("pipeline_data", {})

    # Pull prior analysis from memory for trend comparison
    prior = harness.memory.read(ctx.client_id, Module.SALES_TRAINING, "pipeline analysis", k=1)
    prior_context = prior[0].content if prior else "No prior analysis available."

    response = litellm.completion(
        model="tier-0",
        messages=[{
            "role": "user",
            "content": (
                f"Analyse this HubSpot pipeline. Identify: stuck deals (>14 days in stage), "
                f"velocity trends vs last period, top 3 bottleneck stages.\n\n"
                f"Pipeline: {json.dumps(pipeline, indent=2)[:3000]}\n\n"
                f"Prior analysis: {prior_context[:500]}"
            ),
        }],
        base_url=harness._litellm_base_url,
        api_key=harness._litellm_api_key,
    )

    analysis = {"summary": response.choices[0].message.content}

    harness.memory.write(MemoryRecord(
        client_id=ctx.client_id,
        module=Module.SALES_TRAINING,
        content=json.dumps(analysis),
        metadata={"node": "analyze_pipeline"},
    ))

    return {**state, "analysis": analysis}


def generate_playbook(state: SalesTrainingState) -> SalesTrainingState:
    """Generate stage-by-stage playbook. Tier-1."""
    harness = state["__harness__"]
    analysis = state.get("analysis", {})

    response = litellm.completion(
        model="tier-1",
        messages=[{
            "role": "user",
            "content": (
                "Based on this pipeline analysis, write a concise sales playbook: "
                "recommended actions per deal stage, objection handling for the top 2 stalls, "
                "and one daily ritual for the team.\n\n"
                f"Analysis: {json.dumps(analysis, indent=2)}"
            ),
        }],
        base_url=harness._litellm_base_url,
        api_key=harness._litellm_api_key,
    )

    playbook = {"content": response.choices[0].message.content}
    return {**state, "playbook": playbook}


def generate_daily_guidance(state: SalesTrainingState) -> SalesTrainingState:
    """Generate rep-level coaching cards for today. Tier-1."""
    harness = state["__harness__"]
    pipeline = state.get("pipeline_data", {})
    playbook = state.get("playbook", {})

    deals = pipeline.get("deals", [])[:10]  # Focus on top 10 by priority
    response = litellm.completion(
        model="tier-1",
        messages=[{
            "role": "user",
            "content": (
                "For each of these deals, write a 2-sentence coaching card: "
                "what the rep should do TODAY and why. Reference the playbook.\n\n"
                f"Deals: {json.dumps(deals, indent=2)}\n"
                f"Playbook: {playbook.get('content', '')[:500]}"
            ),
        }],
        base_url=harness._litellm_base_url,
        api_key=harness._litellm_api_key,
    )

    guidance = [{"content": response.choices[0].message.content}]
    return {**state, "daily_guidance": guidance}


def deliver(state: SalesTrainingState) -> SalesTrainingState:
    """Deliver playbook + guidance as HubSpot dashboard notes (stub for OAuth delivery)."""
    # TODO: push playbook to client HubSpot via CRM Notes API
    # POST /crm/v3/objects/notes with { "hs_note_body": playbook, "associations": [...] }
    return {
        **state,
        "__tier_used__": 1,
        "delivery_status": "ok",
        "output": {
            "playbook": state.get("playbook"),
            "daily_guidance": state.get("daily_guidance"),
        },
    }


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_sales_training_graph(harness: "LangGraphHarness") -> Any:
    graph = StateGraph(SalesTrainingState)

    graph.add_node("fetch_pipeline", fetch_pipeline)
    graph.add_node("analyze_pipeline", analyze_pipeline)
    graph.add_node("generate_playbook", generate_playbook)
    graph.add_node("generate_daily_guidance", generate_daily_guidance)
    graph.add_node("deliver", deliver)

    graph.set_entry_point("fetch_pipeline")
    graph.add_edge("fetch_pipeline", "analyze_pipeline")
    graph.add_edge("analyze_pipeline", "generate_playbook")
    graph.add_edge("generate_playbook", "generate_daily_guidance")
    graph.add_edge("generate_daily_guidance", "deliver")
    graph.add_edge("deliver", END)

    return graph.compile()
