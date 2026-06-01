"""
backends.routing — harness selection (blueprint §5.4).

Lives in backends/ (not core/) because it imports concrete backend classes.
Modules call select_harness(profile, ...) and receive an abstract Harness.
"""
from __future__ import annotations

from core import Evaluator, Harness, MemoryService, WorkloadProfile

from .harness_langgraph import LangGraphHarness
from .harness_managed import ManagedAgentsHarness


def select_harness(
    profile: WorkloadProfile,
    *,
    memory: MemoryService,
    evaluator: Evaluator | None = None,
    **kwargs,
) -> Harness:
    """
    Default to self-hosted LangGraph; promote to Managed Agents only when the
    ops burden of running it yourself outweighs the token premium AND none of
    the cheap-routing / batch / model-agnostic constraints apply.

    Extra kwargs are forwarded to the chosen backend constructor (e.g.
    litellm_base_url, db_url for LangGraph; api_key for Managed Agents).
    """
    must_self_host = (
        profile.high_volume
        or profile.cost_sensitive
        or profile.needs_batch
        or profile.model_agnostic
    )
    if must_self_host or not profile.complex_long_running:
        return LangGraphHarness(memory=memory, evaluator=evaluator, **kwargs)
    return ManagedAgentsHarness(memory=memory, evaluator=evaluator, **kwargs)
