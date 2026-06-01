"""
core.harness — the Harness seam.

A harness runs an agent and returns a result. Two backends implement it:

  * LangGraphHarness   — self-hosted, model-agnostic, cost-routed, Batch-capable.
                         The DEFAULT. Use for high-volume / batchable / cost-sensitive work.
  * ManagedAgentsHarness — Claude Managed Agents. Zero-ops, fast, built-in
                         checkpointing/recovery. $0.08/session-hr + standard tokens
                         (no markup, idle free), but Claude-locked and no Batch API.

Modules NEVER instantiate a backend directly. They call `select_harness(profile)`
and depend on the abstract Harness. That keeps the choice reversible per module.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .evaluator import Evaluator
from .memory import MemoryService
from .types import AgentRequest, AgentResult


class Harness(ABC):
    """Executes an AgentRequest. Emits a trace. Reads/writes via MemoryService."""

    def __init__(self, *, memory: MemoryService, evaluator: Evaluator | None = None) -> None:
        self.memory = memory
        self.evaluator = evaluator  # optional online scoring on the result

    @abstractmethod
    def run(self, request: AgentRequest) -> AgentResult:
        """
        Execute one agent. MUST:
          * scope all memory access to request.context.client_id
          * emit a trace (Langfuse) and set trace_id on the result
          * route the model via the LiteLLM gateway starting at context.default_tier,
            escalating only on a failed quality check
          * record run + token_cost to `agent_runs`
        """
        ...


class LangGraphHarness(Harness):
    """
    Self-hosted backend. Runs a compiled LangGraph app on Render workers.
    TODO(claude-code): inject the LiteLLM gateway client + graph registry;
    map AgentRequest.agent_name -> compiled graph; stream state to Postgres.
    """

    def run(self, request: AgentRequest) -> AgentResult:
        raise NotImplementedError("invoke compiled LangGraph app, cost-routed via LiteLLM")


class ManagedAgentsHarness(Harness):
    """
    Claude Managed Agents backend (beta header: managed-agents-2026-04-01).
    TODO(claude-code): create/resume a session, attach the per-tenant memory
    store, run, and tear down. Batch API is NOT available here — do not route
    batchable work to this backend.
    """

    def run(self, request: AgentRequest) -> AgentResult:
        raise NotImplementedError("run via Managed Agents session; standard tokens + $0.08/session-hr")


# --------------------------------------------------------------------------- #
# Routing rule  (blueprint §5.4)
# --------------------------------------------------------------------------- #
@dataclass
class WorkloadProfile:
    """Declared per module. Drives the harness choice."""
    high_volume: bool          # many runs / batchable
    cost_sensitive: bool       # needs Tier-0/1 routing to stay cheap
    needs_batch: bool          # wants the 50% Batch discount
    model_agnostic: bool       # must be able to use non-Claude models
    complex_long_running: bool # benefits from managed checkpointing/recovery


def select_harness(
    profile: WorkloadProfile,
    *,
    memory: MemoryService,
    evaluator: Evaluator | None = None,
) -> Harness:
    """
    Default to self-hosted; promote to Managed Agents only when the ops burden
    of running it yourself outweighs the token premium AND none of the
    cheap-routing / batch / model-agnostic constraints apply.
    """
    must_self_host = (
        profile.high_volume
        or profile.cost_sensitive
        or profile.needs_batch
        or profile.model_agnostic
    )
    if must_self_host or not profile.complex_long_running:
        return LangGraphHarness(memory=memory, evaluator=evaluator)
    return ManagedAgentsHarness(memory=memory, evaluator=evaluator)
