"""
core.harness — the Harness seam.

Modules depend on this abstract class + WorkloadProfile. Concrete backends
(LangGraphHarness, ManagedAgentsHarness) live in backends/ and are never
imported here — keeping core/ dependency-free of any implementation detail.

Routing (select_harness) lives in backends/routing.py where it can safely
import both this interface and the concrete backends without a circular import.
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
        self.evaluator = evaluator

    @abstractmethod
    def run(self, request: AgentRequest) -> AgentResult:
        """
        Execute one agent. MUST:
          * scope all memory access to request.context.client_id
          * emit a Langfuse trace and set trace_id on the result
          * route the model via the LiteLLM gateway starting at context.default_tier,
            escalating only on a failed quality check
          * persist run + token_cost to the agent_runs table
        """
        ...


@dataclass
class WorkloadProfile:
    """Declared per module. Drives the harness choice in backends/routing.py."""
    high_volume: bool           # many runs / batchable
    cost_sensitive: bool        # needs Tier-0/1 routing to stay cheap
    needs_batch: bool           # wants the 50% Batch discount
    model_agnostic: bool        # must be able to use non-Claude models
    complex_long_running: bool  # benefits from managed checkpointing/recovery
