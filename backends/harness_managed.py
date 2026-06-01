"""
backends.harness_managed — Claude Managed Agents harness.

Selective backend. Zero-ops, built-in checkpointing/recovery.
$0.08/session-hr + standard tokens (idle free, no markup).
Claude-locked, no Batch API — use only where ops time > token savings.
See blueprint §5.4 routing rule.

Requires: anthropic>=0.40.0
Beta header: managed-agents-2026-04-01
"""
from __future__ import annotations

import os
import uuid

import anthropic

from core import AgentRequest, AgentResult, Evaluator, Harness, MemoryService, Tier


class ManagedAgentsHarness(Harness):
    """
    Claude Managed Agents backend.

    Each run creates or resumes a Managed Agents session. The per-tenant
    MemoryService is attached as a tool so the agent can read/write memory
    during the session without breaking the abstraction.

    Use for: complex long-running orchestration, automation-critical flows
    where you want zero infra and built-in recovery (e.g. a multi-step
    HubSpot migration that must survive a worker restart mid-run).

    Do NOT use for: batchable/high-volume work, anything needing Tier-0/1
    cost routing, or tasks where model-agnosticism matters.
    """

    # Default model for Managed Agents (always Claude — no Tier-0 routing here)
    MODEL = "claude-opus-4-8"

    def __init__(
        self,
        *,
        memory: MemoryService,
        evaluator: Evaluator | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(memory=memory, evaluator=evaluator)
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ["ANTHROPIC_API_KEY"],
            default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
        )

    def run(self, request: AgentRequest) -> AgentResult:
        run_id = str(uuid.uuid4())

        # Build memory tool definitions so the agent can read/write per-tenant
        memory_tools = self._build_memory_tools(request)

        # Create a managed session and run to completion
        session = self._client.beta.managed_agents.sessions.create(
            model=self.MODEL,
            tools=memory_tools,
        )

        response = self._client.beta.managed_agents.sessions.run(
            session_id=session.id,
            input={
                "agent": request.agent_name,
                "client_id": request.context.client_id,
                "module": request.context.module.value,
                "payload": request.input,
            },
        )

        output = response.output or {}
        token_cost = self._estimate_cost(response)

        # Clean up session (idle sessions are free, but explicit teardown is good hygiene)
        self._client.beta.managed_agents.sessions.delete(session.id)

        return AgentResult(
            output=output,
            run_id=run_id,
            tier_used=Tier.TIER_3,  # Managed Agents always use the top Claude model
            token_cost=token_cost,
            trace_id=getattr(response, "trace_id", None),
        )

    def _build_memory_tools(self, request: AgentRequest) -> list[dict]:
        """Expose MemoryService.read/write/search as tools for the managed session."""
        client_id = request.context.client_id
        module = request.context.module

        return [
            {
                "name": "memory_write",
                "description": "Store a fact worth remembering for this client.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "memory_read",
                "description": "Retrieve relevant memories for this client and module.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "k": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
        ]

    @staticmethod
    def _estimate_cost(response: object) -> float:
        """Estimate USD cost from usage metadata if available."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0.0
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)
        # Opus-4.8 pricing (verify at https://www.anthropic.com/pricing)
        return (input_tokens * 15 + output_tokens * 75) / 1_000_000
