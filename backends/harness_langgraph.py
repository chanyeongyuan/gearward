"""
backends.harness_langgraph — self-hosted LangGraph harness.

Default backend. Model-agnostic, cost-routed via LiteLLM, Batch-capable.
Use for high-volume / batchable / cost-sensitive work (content runs,
migration transforms, sales analysis).

The graph registry maps agent_name -> a compiled LangGraph CompiledGraph.
Register graphs at startup in your application entry point:

    from backends import LangGraphHarness
    from modules.content.graph import build_content_graph

    harness = LangGraphHarness(
        memory=memory_service,
        litellm_base_url=os.environ["LITELLM_BASE_URL"],
        litellm_api_key=os.environ["LITELLM_MASTER_KEY"],
        langfuse_client=langfuse,
        db_url=os.environ["DATABASE_URL"],
    )
    harness.register("content", build_content_graph(harness))
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from langfuse import Langfuse

from core import AgentRequest, AgentResult, Evaluator, Harness, MemoryService, Tier


class LangGraphHarness(Harness):
    """
    Runs compiled LangGraph graphs on Render workers.

    Each graph is compiled with the LiteLLM gateway as the model backend so
    tiered cost routing is enforced automatically. All graph state is persisted
    to Postgres between steps (stateless workers — blueprint §9).
    """

    def __init__(
        self,
        *,
        memory: MemoryService,
        evaluator: Evaluator | None = None,
        litellm_base_url: str,
        litellm_api_key: str,
        langfuse_client: Langfuse,
        db_url: str,
    ) -> None:
        super().__init__(memory=memory, evaluator=evaluator)
        self._litellm_base_url = litellm_base_url
        self._litellm_api_key = litellm_api_key
        self._langfuse = langfuse_client
        self._db_url = db_url
        self._graphs: dict[str, Any] = {}  # agent_name -> CompiledGraph

    def register(self, agent_name: str, graph: Any) -> None:
        """Register a compiled LangGraph. Call at startup before serving requests."""
        self._graphs[agent_name] = graph

    def litellm_model(self, tier: Tier) -> str:
        """Map a Tier enum to the LiteLLM model alias for this gateway instance."""
        return f"{self._litellm_base_url}/v1/chat/completions#{tier.name.lower()}"

    def run(self, request: AgentRequest) -> AgentResult:
        run_id = str(uuid.uuid4())
        trace = self._langfuse.trace(
            name=request.agent_name,
            user_id=request.context.client_id,
            metadata={
                "module": request.context.module.value,
                "run_id": run_id,
            },
        )

        graph = self._graphs.get(request.agent_name)
        if graph is None:
            raise KeyError(
                f"No graph registered for agent '{request.agent_name}'. "
                "Call harness.register(name, compiled_graph) at startup."
            )

        # Inject harness context into graph input so nodes can read/write memory
        # and route model calls via the correct tier.
        graph_input = {
            **request.input,
            "__harness__": self,
            "__context__": request.context,
            "__trace_id__": trace.id,
        }

        try:
            result_state = graph.invoke(graph_input)
            output = {k: v for k, v in result_state.items() if not k.startswith("__")}
            token_cost = result_state.get("__token_cost__", 0.0)
            tier_used = result_state.get("__tier_used__", request.context.default_tier)

            self._persist_run(
                run_id=run_id,
                request=request,
                output=output,
                token_cost=token_cost,
                tier_used=tier_used,
                trace_id=trace.id,
                status="done",
            )

            result = AgentResult(
                output=output,
                run_id=run_id,
                tier_used=tier_used,
                token_cost=token_cost,
                trace_id=trace.id,
            )

            if self.evaluator and request.context.module.value == "content":
                from core import CONTENT_DIMENSIONS
                eval_result = self.evaluator.score_online(result, CONTENT_DIMENSIONS)
                result.output["__eval__"] = eval_result

            trace.update(output={"status": "done", "token_cost": token_cost})
            return result

        except Exception as exc:
            self._persist_run(
                run_id=run_id,
                request=request,
                output=None,
                token_cost=0.0,
                tier_used=request.context.default_tier,
                trace_id=trace.id,
                status="failed",
                error=str(exc),
            )
            trace.update(output={"status": "failed", "error": str(exc)})
            raise

    def _persist_run(
        self,
        *,
        run_id: str,
        request: AgentRequest,
        output: dict | None,
        token_cost: float,
        tier_used: Tier,
        trace_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        import json
        with psycopg.connect(self._db_url) as conn:
            conn.execute(
                """
                INSERT INTO agent_runs
                    (id, client_id, module, agent_name, status, input, output,
                     token_cost, model_tier, trace_id, error, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    request.context.client_id,
                    request.context.module.value,
                    request.agent_name,
                    status,
                    json.dumps(request.input),
                    json.dumps(output) if output else None,
                    token_cost,
                    tier_used.value,
                    trace_id,
                    error,
                    datetime.now(timezone.utc),
                ),
            )
